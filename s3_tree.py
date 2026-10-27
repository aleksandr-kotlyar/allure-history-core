"""Read the navigation metadata needed by an incremental S3 publisher."""

from __future__ import annotations

import json
from pathlib import Path

from botocore.exceptions import ClientError

import generate_index as gui
from prune_reports import job_id as retention_id


def relative_key(key: str) -> str:
    parts = key.split("/")
    if (not key or any(part in {"", ".", ".."} for part in parts)
            or "\\" in key or any(ord(char) < 32 for char in key)):
        raise ValueError(f"Unsafe relative object key: {key!r}")
    return key


def inventory(client, bucket: str, prefix: str, *, include_markers: bool = False) -> dict:
    objects = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"][len(prefix):]
            if not key or key.endswith("/"):
                if include_markers:
                    if key:
                        relative_key(key.removesuffix("/"))
                    objects[key] = item
            elif key != ".allure-history-index.lock":
                objects[relative_key(key)] = item
    return objects


def directory_listing(client, bucket: str, prefix: str) -> tuple[dict, set[str]]:
    """List one level; S3 collapses report assets into CommonPrefixes."""
    files, folders = {}, set()
    pages = client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")
    for page in pages:
        for item in page.get("Contents", []):
            key = item["Key"][len(prefix):]
            if key and not key.endswith("/") and key != ".allure-history-index.lock":
                files[relative_key(key)] = item
        for item in page.get("CommonPrefixes", []):
            key = relative_key(item["Prefix"][len(prefix):].removesuffix("/"))
            if "/" in key:
                raise ValueError(f"Expected one directory level: {key!r}")
            folders.add(key)
    return files, folders


def listing_version(listing: tuple[dict, set[str]]) -> tuple:
    files, folders = listing
    return sorted((key, item["ETag"]) for key, item in files.items()), sorted(folders)


def navigation_metadata(folder: Path) -> bytes:
    latest = gui.latest_report_for(folder)
    record = {"version": 1, "modified_at": gui.read_modified_at(folder), "latest": None}
    if latest is not None:
        modified = gui.read_modified_at(latest)
        record["latest"] = {"path": latest.relative_to(folder).as_posix(), "modified_at": modified}
        record["modified_at"] = record["modified_at"] or modified
    return (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")


class SparseTree:
    """Materialize small metadata and remember exactly which sources were read.

    Updated groups and their ancestors are always listed live. Other subtrees
    need only their cached latest-report pointer. Legacy subtrees are discovered
    once with delimiter listings, without reading report HTML or attachments.
    """

    def __init__(self, client, bucket: str, prefix: str, public: Path, group: Path,
                 report_name: str, keep: int):
        self.client, self.bucket, self.prefix = client, bucket, prefix
        self.public, self.group = public, group
        self.report_name, self.keep = report_name, keep
        self.listings = {}
        self.metadata_etags = {}
        self.cache_folders = set()
        self.expired = set()
        self.force = {Path(), group, *group.parents}

    def key(self, folder: Path, name: str = "") -> str:
        relative = (folder / name).as_posix()
        return self.prefix + (relative if relative != "." else "")

    def read_metadata(self, key: str, limit: int = 2_000_000) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        try:
            data = response["Body"].read(limit + 1)
        finally:
            response["Body"].close()
        if len(data) > limit:
            raise ValueError(f"S3 navigation metadata is too large: {key}")
        self.metadata_etags[key] = response["ETag"]
        return data

    def restore_cached(self, folder: Path) -> bool:
        data = self.read_metadata(self.key(folder, gui.NAVIGATION_METADATA_FILENAME))
        if data is None:
            return False
        record = json.loads(data)
        if not isinstance(record, dict) or record.get("version") != 1:
            raise ValueError(f"Unsupported S3 navigation metadata: {folder}")
        local = self.public / folder
        modified = record.get("modified_at", "")
        if not isinstance(modified, str):
            raise ValueError(f"Invalid modification time in S3 navigation metadata: {folder}")
        (local / gui.MODIFIED_AT_FILENAME).write_text(modified, encoding="utf-8")
        latest = record.get("latest")
        if latest is not None:
            if (not isinstance(latest, dict) or not isinstance(latest.get("path"), str)
                    or not isinstance(latest.get("modified_at"), str)):
                raise ValueError(f"Invalid latest report in S3 navigation metadata: {folder}")
            path = Path(relative_key(latest["path"]))
            if any(part in gui.HIDDEN_INDEX_ENTRIES for part in path.parts):
                raise ValueError(f"Invalid latest report path in S3 navigation metadata: {folder}")
            report = local / path
            (report / "widgets").mkdir(parents=True, exist_ok=True)
            (report / gui.INDEX_FILENAME).touch()
            (report / gui.MODIFIED_AT_FILENAME).write_text(latest["modified_at"], encoding="utf-8")
        return True

    def restore(self) -> None:
        self.restore_folder(Path())
        # Missing groups still need live listings and metadata for their parents.
        for folder in sorted(self.force, key=lambda value: len(value.parts)):
            if folder not in self.listings:
                self.restore_folder(folder)

    def restore_folder(self, folder: Path) -> None:
        local = self.public / folder
        local.mkdir(parents=True, exist_ok=True)
        if folder not in self.force and self.restore_cached(folder):
            return
        listing = directory_listing(self.client, self.bucket, self.key(folder).rstrip("/") + "/")
        self.listings[folder] = listing
        files, folders = listing
        scope = Path(self.prefix.rstrip("/")) if folder == Path() else folder
        is_report = (gui.job_id(scope) is not None or gui.report_datetime(scope) is not None
                     or (gui.INDEX_FILENAME in files and bool(folders & {"widgets", "data"})))
        if is_report:
            if folder in self.force:
                raise ValueError("--s3-uri and report group must select a navigation root, not an Allure report")
            (local / "widgets").mkdir(exist_ok=True)
            (local / gui.INDEX_FILENAME).touch()
            summary = self.read_metadata(self.key(folder, gui.SUMMARY_FILENAME))
            if summary is not None:
                (local / gui.SUMMARY_FILENAME).write_bytes(summary)
        else:
            self.cache_folders.add(folder)
            for name in files.keys() - gui.HIDDEN_INDEX_ENTRIES:
                (local / name).touch()

        if gui.MODIFIED_AT_FILENAME in files:
            data = self.read_metadata(self.key(folder, gui.MODIFIED_AT_FILENAME), 64_000)
            if data is not None:
                (local / gui.MODIFIED_AT_FILENAME).write_bytes(data)
        if is_report:
            if not gui.read_modified_at(local) and gui.INDEX_FILENAME in files:
                (local / gui.MODIFIED_AT_FILENAME).write_text(files[gui.INDEX_FILENAME]["LastModified"].isoformat())
            return

        if folder == self.group:
            jobs = sorted({name for name in folders if name.startswith("job_")} | {self.report_name},
                          key=lambda name: (retention_id(Path(name)), name))
            self.expired = set(jobs[:-self.keep])
            if self.report_name in self.expired:
                raise ValueError("The new report ID falls outside REPORTS_TO_KEEP; use a newer unique ID")
        for name in sorted(folders - gui.HIDDEN_INDEX_ENTRIES):
            child = folder / name
            if folder == self.group and (name in self.expired or name == self.report_name):
                # Core will replace the current snapshot and prune these markers.
                (self.public / child / "widgets").mkdir(parents=True, exist_ok=True)
                (self.public / child / gui.INDEX_FILENAME).touch()
            else:
                self.restore_folder(child)

    def check_unchanged(self) -> None:
        for folder, before in self.listings.items():
            current = directory_listing(self.client, self.bucket, self.key(folder).rstrip("/") + "/")
            if listing_version(current) != listing_version(before):
                raise RuntimeError("S3 navigation changed during generation; refusing to publish")
        for key, etag in self.metadata_etags.items():
            self.client.head_object(Bucket=self.bucket, Key=key, IfMatch=etag)

    def write_metadata(self) -> list[Path]:
        paths = []
        for folder in sorted(self.cache_folders):
            local = self.public / folder
            destination = local / gui.NAVIGATION_METADATA_FILENAME
            destination.write_bytes(navigation_metadata(local))
            paths.append(destination)
        return paths
