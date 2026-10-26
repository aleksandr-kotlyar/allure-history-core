"""Preview, conditionally publish, and roll back navigation in an existing S3 tree."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from allure_history import http_base_url, index_reports
import generate_index as gui


LOCK_NAME = ".allure-history-index.lock"
HTML_HEADERS = {"ContentType": "text/html; charset=utf-8", "CacheControl": "no-cache"}
BACKUP_HEADERS = ("ContentType", "CacheControl", "ContentEncoding", "ContentDisposition",
                  "ContentLanguage", "Metadata", "WebsiteRedirectLocation")


def relative_key(key: str) -> str:
    parts = key.split("/")
    if (not key or any(part in {"", ".", ".."} for part in parts)
            or "\\" in key or any(ord(char) < 32 for char in key)):
        raise ValueError(f"Unsafe relative object key: {key!r}")
    return key


def storage_location(uri: str) -> tuple[str, str]:
    if any(ord(char) < 32 for char in uri):
        raise ValueError("--s3-uri must not contain control characters")
    parsed = urlsplit(uri)
    if (parsed.scheme != "s3" or not parsed.netloc or parsed.username
            or ":" in parsed.netloc or parsed.query or parsed.fragment):
        raise ValueError("--s3-uri must be s3://bucket[/prefix]")
    prefix = parsed.path.removeprefix("/").removesuffix("/")
    if prefix:
        relative_key(prefix)
    elif parsed.path not in {"", "/"}:
        raise ValueError("--s3-uri must not contain empty path segments")
    return parsed.netloc, prefix + "/" if prefix else ""


def inventory(client, bucket: str, prefix: str) -> dict:
    objects = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"][len(prefix):]
            if key and not key.endswith("/") and key != LOCK_NAME:
                objects[relative_key(key)] = item
    return objects


def fingerprint(objects: dict, navigation: list[str]) -> str:
    ignored = set(navigation)
    values = sorted((key, item["ETag"]) for key, item in objects.items() if key not in ignored)
    return sha256(json.dumps(values).encode()).hexdigest()


def read_object(client, bucket: str, key: str) -> tuple[bytes, dict]:
    response = client.get_object(Bucket=bucket, Key=key)
    try:
        return response["Body"].read(), response
    finally:
        response["Body"].close()


def materialize(client, bucket: str, prefix: str, objects: dict, source: Path) -> list[str]:
    """Fetch metadata only; report index bodies and attachments stay in S3."""
    markers = set()
    for key in objects:
        parts = PurePosixPath(key).parts
        for position, part in enumerate(parts[:-1]):
            if part in {"widgets", "data"}:
                markers.add(PurePosixPath(*parts[:position]).as_posix())
    reports = []
    report_paths = set()
    for key in sorted(objects, key=lambda value: (value.count("/"), value)):
        path = PurePosixPath(key)
        parent = path.parent
        if path.name != gui.INDEX_FILENAME or parent == PurePosixPath("."):
            continue
        if any(part in {"history", ".history", "latest"} for part in parent.parts):
            continue
        if any(ancestor.as_posix() in report_paths for ancestor in parent.parents):
            continue
        if (gui.job_id(Path(parent.name)) is not None
                or gui.report_datetime(Path(parent.name)) is not None
                or parent.as_posix() in markers):
            reports.append(parent.as_posix())
            report_paths.add(parent.as_posix())
            local = source / parent
            local.mkdir(parents=True, exist_ok=True)
            (local / gui.INDEX_FILENAME).touch()
            (local / "widgets").mkdir(exist_ok=True)
            for suffix, limit in ((gui.SUMMARY_FILENAME, 2_000_000),
                                  (gui.MODIFIED_AT_FILENAME, 64_000)):
                metadata_key = (parent / suffix).as_posix()
                item = objects.get(metadata_key)
                if item is None or item.get("Size", 0) > limit:
                    continue
                data, response = read_object(client, bucket, prefix + metadata_key)
                if response["ETag"] != item["ETag"]:
                    raise RuntimeError("Report metadata changed while preparing; create a new plan")
                destination = source / metadata_key
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            if not gui.read_modified_at(local):
                # Use the object's actual modification time, never the download time.
                (local / gui.MODIFIED_AT_FILENAME).write_text(objects[key]["LastModified"].isoformat())
    # Keep empty neighbouring navigation groups, without traversing report internals.
    for key in objects:
        path = PurePosixPath(key)
        if any(parent.as_posix() in report_paths for parent in path.parents):
            continue
        if path.name == gui.INDEX_FILENAME:
            (source / path.parent).mkdir(parents=True, exist_ok=True)
        for position, part in enumerate(path.parts[:-1]):
            if part in {".history", "history"}:
                (source / PurePosixPath(*path.parts[:position])).mkdir(parents=True, exist_ok=True)
                break
    source.mkdir(parents=True, exist_ok=True)
    return reports


def save_plan(path: Path, plan: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare(args, client) -> Path:
    bucket, prefix = storage_location(args.s3_uri)
    http_base_url(args.public_url)
    output = Path(args.output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output-dir must be empty; use a new directory for each plan")
    output.mkdir(parents=True, exist_ok=True)
    objects = inventory(client, bucket, prefix)
    scope_name = Path(prefix.rstrip("/")).name
    if "index.html" in objects and (
            gui.job_id(Path(scope_name)) is not None
            or gui.report_datetime(Path(scope_name)) is not None
            or any(key.startswith(("widgets/", "data/")) for key in objects)):
        raise ValueError("--s3-uri must select a navigation root, not an Allure report")
    reports = materialize(client, bucket, prefix, objects, output / "source")
    title = args.public_title
    intro = os.environ.get(gui.SHOW_INTRO_ENV)
    try:
        if intro is None:
            os.environ[gui.SHOW_INTRO_ENV] = "false"
        index_reports(argparse.Namespace(public_dir=str(output / "source"),
                                         output_dir=str(output / "public"),
                                         report_base_url=args.public_url, public_title=title))
    finally:
        if intro is None:
            os.environ.pop(gui.SHOW_INTRO_ENV, None)

    navigation = sorted(path.relative_to(output / "public").as_posix()
                        for path in (output / "public").rglob("*.html"))
    operations = []
    for key in navigation:
        data = (output / "public" / key).read_bytes()
        previous = None
        if key in objects:
            body, response = read_object(client, bucket, prefix + key)
            if response["ETag"] != objects[key]["ETag"]:
                raise RuntimeError("Navigation changed while preparing; create a new plan")
            headers = {name: response[name] for name in BACKUP_HEADERS if name in response}
            if body == data and all(headers.get(k) == v for k, v in HTML_HEADERS.items()):
                continue
            backup = output / "backup" / key
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(body)
            previous = {"etag": response["ETag"], "sha256": sha256(body).hexdigest(), "headers": headers}
        operations.append({"key": key, "action": "replace" if previous else "create",
                           "sha256": sha256(data).hexdigest(), "previous": previous})
    plan = {"version": 1, "s3_uri": args.s3_uri, "public_url": args.public_url,
            "reports": reports, "navigation": navigation,
            "source_fingerprint": fingerprint(objects, navigation), "state": "prepared",
            "operations": operations}
    path = output / "plan.json"
    save_plan(path, plan)
    print(f"Prepared {len(operations)} navigation change(s): {path}")
    return path


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("version") != 1:
        raise ValueError("Unsupported plan version")
    storage_location(plan["s3_uri"])
    reports = set(plan["reports"])
    for entry in plan["operations"]:
        key = relative_key(entry["key"])
        parts = PurePosixPath(key).parts
        if (parts[-1] != "index.html" or any(p in {"history", ".history"} for p in parts)
                or any(parent.as_posix() in reports for parent in PurePosixPath(key).parents)):
            raise ValueError(f"Plan would modify a report or history: {key}")
        for directory, expected in (("public", entry["sha256"]),
                                    ("backup", (entry.get("previous") or {}).get("sha256"))):
            if expected is not None:
                file = (path.parent / directory / key).resolve()
                if not file.is_relative_to(path.parent.resolve()) or sha256(file.read_bytes()).hexdigest() != expected:
                    raise ValueError(f"Plan file changed: {directory}/{key}")
    return plan


def rollback_targets(plan: dict, client, bucket: str, prefix: str, objects: dict) -> dict:
    targets = {}
    for entry in plan["operations"]:
        if not entry.get("attempted") or entry.get("rolled_back"):
            continue
        key = entry["key"]
        previous = entry["previous"]
        current = objects.get(key)
        if current is None:
            if previous is None:
                targets[key] = None
                continue
            raise RuntimeError(f"Refusing to restore an externally deleted object: {key}")
        body, response = read_object(client, bucket, prefix + key)
        digest = sha256(body).hexdigest()
        if previous and digest == previous["sha256"] and all(
                response.get(name) == previous["headers"].get(name) for name in BACKUP_HEADERS):
            targets[key] = None
        elif (digest == entry["sha256"]
              and (not entry.get("published_etag") or response["ETag"] == entry["published_etag"])
              and all(response.get(name) == HTML_HEADERS.get(name)
                      for name in BACKUP_HEADERS if name != "Metadata")
              and not response.get("Metadata")):
            targets[key] = response["ETag"]
        else:
            raise RuntimeError(f"Refusing to roll back changed object: {key}")
    return targets


@contextmanager
def publish_lock(client, bucket: str, prefix: str):
    # No expiry takeover: a killed publisher must be inspected before its lock is removed.
    response = client.put_object(Bucket=bucket, Key=prefix + LOCK_NAME,
                                 Body=uuid4().hex.encode(), IfNoneMatch="*")
    try:
        yield
    finally:
        client.delete_object(Bucket=bucket, Key=prefix + LOCK_NAME, IfMatch=response["ETag"])


def apply_plan(path: Path, client, *, rollback: bool = False) -> None:
    plan = load_plan(path)
    bucket, prefix = storage_location(plan["s3_uri"])
    if not rollback and plan["state"] != "prepared":
        raise ValueError("Plan was already attempted; roll it back or create a new plan")
    with publish_lock(client, bucket, prefix):
        objects = inventory(client, bucket, prefix)
        if not rollback:
            if fingerprint(objects, plan["navigation"]) != plan["source_fingerprint"]:
                raise RuntimeError("Source tree changed; create a new plan")
            for entry in plan["operations"]:
                expected = (entry["previous"] or {}).get("etag")
                if objects.get(entry["key"], {}).get("ETag") != expected:
                    raise RuntimeError("Navigation changed; create a new plan")
        targets = rollback_targets(plan, client, bucket, prefix, objects) if rollback else {}
        plan["state"] = "rolling-back" if rollback else "publishing"
        save_plan(path, plan)
        for entry in reversed(plan["operations"]) if rollback else plan["operations"]:
            key = entry["key"]
            if rollback:
                if key not in targets:
                    continue
                if targets[key] is None:
                    entry["rolled_back"] = True
                    save_plan(path, plan)
                    continue
                if entry["previous"]:
                    client.put_object(Bucket=bucket, Key=prefix + key,
                                      Body=(path.parent / "backup" / key).read_bytes(),
                                      IfMatch=targets[key], **entry["previous"]["headers"])
                else:
                    client.delete_object(Bucket=bucket, Key=prefix + key, IfMatch=targets[key])
                entry["rolled_back"] = True
            else:
                entry["attempted"] = True
                save_plan(path, plan)
                condition = {"IfMatch": entry["previous"]["etag"]} if entry["previous"] else {"IfNoneMatch": "*"}
                response = client.put_object(Bucket=bucket, Key=prefix + key,
                                             Body=(path.parent / "public" / key).read_bytes(),
                                             **HTML_HEADERS, **condition)
                entry["published_etag"] = response["ETag"]
            save_plan(path, plan)
        plan["state"] = "rolled-back" if rollback else "published"
        save_plan(path, plan)
    print(f"Navigation {plan['state']}: {path}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Read S3 metadata and prepare a local preview and rollback plan")
    index.add_argument("--s3-uri", required=True)
    index.add_argument("--public-url", required=True)
    index.add_argument("--output-dir", required=True)
    index.add_argument("--public-title", default=os.environ.get("ALLURE_HISTORY_PUBLIC_TITLE", "allure-history"))
    for name in ("publish", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        client = boto3.client("s3")
        if args.command == "index":
            prepare(args, client)
        else:
            apply_plan(args.plan.resolve(), client, rollback=args.command == "rollback")
        return 0
    except (OSError, ValueError, RuntimeError, BotoCoreError, ClientError) as error:
        print(f"allure-history-s3: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
