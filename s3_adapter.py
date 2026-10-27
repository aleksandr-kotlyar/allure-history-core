"""Publish Allure report history and manage navigation in S3-compatible storage."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from allure_history import http_base_url, index_reports
import generate_index as gui
from report_paths import path_segment, report_group_path
from s3_tree import inventory, navigation_metadata, relative_key, SparseTree


LOCK_NAME = ".allure-history-index.lock"
HTML_HEADERS = {"ContentType": "text/html; charset=utf-8", "CacheControl": "no-cache"}
BACKUP_HEADERS = ("ContentType", "CacheControl", "ContentEncoding", "ContentDisposition",
                  "ContentLanguage", "Metadata", "WebsiteRedirectLocation")


def navigation_headers(key: str) -> dict:
    if PurePosixPath(key).name == gui.NAVIGATION_METADATA_FILENAME:
        return {"ContentType": "application/json", "CacheControl": "no-cache"}
    return HTML_HEADERS


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


def validate_navigation_root(prefix: str, objects: dict) -> None:
    scope_name = Path(prefix.rstrip("/")).name
    if "index.html" in objects and (
            gui.job_id(Path(scope_name)) is not None
            or gui.report_datetime(Path(scope_name)) is not None
            or any(key.startswith(("widgets/", "data/")) for key in objects)):
        raise ValueError("--s3-uri must select a navigation root, not an Allure report")


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
    validate_navigation_root(prefix, objects)
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

    for folder in gui.iter_index_folders(output / "source"):
        relative = folder.relative_to(output / "source") / gui.NAVIGATION_METADATA_FILENAME
        (output / "public" / relative).write_bytes(navigation_metadata(folder))
    navigation = sorted(path.relative_to(output / "public").as_posix()
                        for path in (output / "public").rglob("*") if path.is_file())
    operations = []
    for key in navigation:
        data = (output / "public" / key).read_bytes()
        previous = None
        if key in objects:
            body, response = read_object(client, bucket, prefix + key)
            if response["ETag"] != objects[key]["ETag"]:
                raise RuntimeError("Navigation changed while preparing; create a new plan")
            headers = {name: response[name] for name in BACKUP_HEADERS if name in response}
            if body == data and all(headers.get(k) == v for k, v in navigation_headers(key).items()):
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
        if (parts[-1] not in {"index.html", gui.NAVIGATION_METADATA_FILENAME}
                or any(p in {"history", ".history"} for p in parts)
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
              and all(response.get(name) == navigation_headers(key).get(name)
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
                                             **navigation_headers(key), **condition)
                entry["published_etag"] = response["ETag"]
            save_plan(path, plan)
        plan["state"] = "rolled-back" if rollback else "published"
        save_plan(path, plan)
    print(f"Navigation {plan['state']}: {path}")


def transfer_files(operation, files) -> None:
    with ThreadPoolExecutor(max_workers=8) as executor:
        for _ in executor.map(operation, files):
            pass


def publish_job(args, client) -> None:
    from boto3.s3.transfer import TransferConfig

    if args.provider == "github":
        import github_adapter as provider
    else:
        import gitlab_adapter as provider

    bucket, prefix = storage_location(args.s3_uri)
    if not prefix:
        raise ValueError("Report publication requires a dedicated, non-root S3 prefix")
    public_url = http_base_url(args.public_url)
    keep = int(provider.env_or("REPORTS_TO_KEEP", "30"))
    if keep < 1:
        raise ValueError("REPORTS_TO_KEEP must be positive for report publication")
    provider.validate_runtime_commands()
    provider.ensure_results_present()
    public = Path(args.public_dir).resolve()
    if public.exists() and any(public.iterdir()):
        raise ValueError("--public-dir must be empty; use a clean report workspace")
    public.mkdir(parents=True, exist_ok=True)
    config = TransferConfig(use_threads=False)
    group = report_group_path(os.environ.get("ALLURE_HISTORY_GROUP_PATH", ""),
                              provider.report_env(), provider.report_branch())
    report_name = "job_" + path_segment(provider.job_id(), "report ID")

    def download(key):
        destination = public / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, prefix + key, str(destination), Config=config)

    def upload(path):
        key = path.relative_to(public).as_posix()
        content_type, encoding = mimetypes.guess_type(path.name)
        headers = {"ContentType": content_type or "application/octet-stream"}
        if encoding:
            headers["ContentEncoding"] = encoding
        if path.suffix == ".html":
            headers.update(HTML_HEADERS)
        elif path.name == gui.NAVIGATION_METADATA_FILENAME:
            headers.update(navigation_headers(key))
        client.upload_file(str(path), bucket, prefix + key, ExtraArgs=headers, Config=config)

    with publish_lock(client, bucket, prefix):
        tree = SparseTree(client, bucket, prefix, public, group, report_name, keep)
        tree.restore()
        # Recursive listings are limited to data we replace or explicitly expire.
        scopes = [(group / "history").as_posix(), (group / report_name).as_posix()]
        scopes.extend((group / name).as_posix() for name in sorted(tree.expired))
        previous = {scope: inventory(client, bucket, prefix + scope + "/", include_markers=True)
                    for scope in scopes}
        history_scope = (group / "history").as_posix()
        transfer_files(download, (history_scope + "/" + key for key in previous[history_scope]
                                  if key and not key.endswith("/")))
        previous_intro = os.environ.get(gui.SHOW_INTRO_ENV)
        previous_title = gui.PUBLIC_TITLE
        try:
            os.environ.setdefault(gui.SHOW_INTRO_ENV, "false")
            gui.PUBLIC_TITLE = args.public_title
            provider.build_report(public_dir=public, base_url=public_url,
                                  public_title=args.public_title, skip_index=True)
            for folder in sorted(tree.force, key=lambda value: len(value.parts), reverse=True):
                gui.index_folder(public / folder, root=public)
            metadata = tree.write_metadata()
        finally:
            gui.PUBLIC_TITLE = previous_title
            if previous_intro is None:
                os.environ.pop(gui.SHOW_INTRO_ENV, None)
        result = json.loads(provider.output_file_path().read_text(encoding="utf-8"))
        snapshot = Path(result["snapshot_dir"])
        if snapshot != public / group / report_name or not (snapshot / "index.html").is_file():
            raise RuntimeError("Core did not produce a report inside the public tree")
        tree.check_unchanged()
        for scope, objects in previous.items():
            current = inventory(client, bucket, prefix + scope + "/", include_markers=True)
            if fingerprint(current, []) != fingerprint(objects, []):
                raise RuntimeError("S3 report/history changed during generation; refusing to publish")

        # Publish report assets before its entry page, and prune only after navigation succeeds.
        transfer_files(upload, (path for path in snapshot.rglob("*")
                                if path.is_file() and path != snapshot / "index.html"))
        upload(snapshot / "index.html")
        history = snapshot.parent / "history"
        transfer_files(upload, (path for path in history.rglob("*") if path.is_file()))
        navigation = set()
        for relative in tree.force:
            folder = public / relative
            for name in ("index.html", "latest/index.html", ".modified_at"):
                path = folder / name
                if path.is_file():
                    navigation.add(path)
        transfer_files(upload, sorted(navigation))
        transfer_files(upload, metadata)

        def delete(item):
            key, etag = item
            client.delete_object(Bucket=bucket, Key=prefix + key, IfMatch=etag)

        for scope, objects in previous.items():
            current = {path.relative_to(public / scope).as_posix()
                       for path in (public / scope).rglob("*") if path.is_file()}
            transfer_files(delete, ((scope + "/" + key, objects[key]["ETag"])
                                    for key in sorted(objects.keys() - current)))

    if args.provider == "github":
        provider.write_outputs(public)
        provider.comment_pr()
    else:
        provider.comment_mr()
    print(f"Published Allure report to {result['report_url']}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (("index", "Prepare a navigation preview and rollback plan"),
                            ("refresh-gui", "Explicitly refresh GUI across the selected archive")):
        index = commands.add_parser(name, help=help_text)
        index.add_argument("--s3-uri", required=True)
        index.add_argument("--public-url", required=True)
        index.add_argument("--output-dir", required=True)
        index.add_argument("--public-title", default=os.environ.get("ALLURE_HISTORY_PUBLIC_TITLE", "allure-history"))
    job = commands.add_parser("publish-job", help="Restore, generate, retain and publish reports using S3 storage")
    job.add_argument("--provider", choices=("gitlab", "github"), required=True)
    job.add_argument("--s3-uri", required=True)
    job.add_argument("--public-url", required=True)
    job.add_argument("--public-dir", default="allure-s3/public")
    job.add_argument("--public-title", default=os.environ.get("ALLURE_HISTORY_PUBLIC_TITLE", "allure-history"))
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
        if args.command in {"index", "refresh-gui"}:
            plan = prepare(args, client)
            if args.command == "refresh-gui":
                apply_plan(plan, client)
        elif args.command == "publish-job":
            publish_job(args, client)
        else:
            apply_plan(args.plan.resolve(), client, rollback=args.command == "rollback")
        return 0
    except (OSError, ValueError, RuntimeError, BotoCoreError, ClientError) as error:
        print(f"allure-history-s3: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
