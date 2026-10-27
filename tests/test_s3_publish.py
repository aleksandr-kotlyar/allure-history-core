import argparse
from copy import deepcopy
import json
from pathlib import Path

from botocore.exceptions import ClientError
import pytest

import allure_history
import github_adapter
import gitlab_adapter
import generate_index as gui
import s3_adapter as s3
from tests.test_s3_adapter import MemoryS3


GROUP = "dev/project/browser name"
ROOT = f"archive/{GROUP}"


class PublishingS3(MemoryS3):
    def download_file(self, bucket, key, filename, **kwargs):
        Path(filename).write_bytes(self.get_object(Bucket=bucket, Key=key)["Body"].read())

    def upload_file(self, filename, bucket, key, *, ExtraArgs, **kwargs):
        self.put_object(Bucket=bucket, Key=key, Body=Path(filename).read_bytes(), **ExtraArgs)


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = PublishingS3()
    for number in (1, 2):
        client.add(f"{ROOT}/job_{number}/index.html", b"old report", ContentType="text/html")
        client.add(f"{ROOT}/job_{number}/data/log.txt", b"old attachment")
    client.add(f"{ROOT}/history/history.json", b'"previous trend"')
    client.add(f"{ROOT}/history/obsolete.json", b"obsolete")
    client.add("archive/other/main/job_50/index.html", b"neighbour report", Metadata={"owner": "other"})
    client.add("archive/other/main/job_50/widgets/summary.json", b"{}")
    client.add("archive/other/index.html", b"old neighbour GUI")
    client.add("archive/other/main/index.html", b"old neighbour group GUI")
    client.add("archive/manual.txt", b"keep this file", ContentType="text/plain")
    client.add("outside/keep.bin", b"other prefix")
    results = tmp_path / "allure-results"
    results.mkdir()
    (results / "result.json").write_text("{}")
    monkeypatch.setenv("ALLURE_HISTORY_GROUP_PATH", GROUP)
    monkeypatch.setenv("REPORTS_TO_KEEP", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.setenv("GITHUB_RUN_ID", "3")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setenv("CI_JOB_ID", "3")
    monkeypatch.setenv("CI_PIPELINE_URL", "https://ci.test/pipelines/3")
    monkeypatch.delenv("CI_PAGES_URL", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GIT_PUSH_TOKEN", raising=False)
    restored = []
    comments = []

    def generate(command, **kwargs):
        previous = results / "history/history.json"
        restored.append(json.loads(previous.read_text()) if previous.is_file() else None)
        output = Path(command[4])
        (output / "history").mkdir(parents=True)
        (output / "widgets").mkdir()
        (output / "data").mkdir()
        (output / "index.html").write_text("<html>new report</html>")
        (output / "app.js").write_text("const report = 3;")
        (output / "data/log.txt").write_text("new attachment")
        (output / "history/history.json").write_text('"new trend"')
        (output / "widgets/summary.json").write_text('{"statistic":{"passed":1,"total":1}}')

    def run_core(command, **kwargs):
        if allure_history.main(command[1:]) != 0:
            raise RuntimeError("Core generation failed")

    monkeypatch.setattr(allure_history.subprocess, "run", generate)
    for provider in (github_adapter, gitlab_adapter):
        monkeypatch.setattr(provider, "validate_runtime_commands", lambda: None)
        monkeypatch.setattr(provider, "run_command", run_core)
    monkeypatch.setattr(github_adapter, "comment_pr", lambda: comments.append(github_adapter.report_url()))
    monkeypatch.setattr(gitlab_adapter, "comment_mr", lambda: comments.append(gitlab_adapter.report_url()))
    args = argparse.Namespace(provider="github", s3_uri="s3://reports/archive", public_url="https://reports.test/base",
                              public_dir="allure-s3/public", public_title="Custom reports")
    return args, client, restored, comments


@pytest.mark.parametrize("provider", ["gitlab", "github"])
def test_s3_job_restores_builds_retains_publishes_and_comments_without_git(publication, provider):
    args, client, restored, comments = publication
    args.provider = provider
    before = deepcopy(client.objects)

    s3.publish_job(args, client)

    assert restored == ["previous trend"]
    assert client.objects[f"{ROOT}/job_3/index.html"]["body"] == b"<html>new report</html>"
    assert client.objects[f"{ROOT}/job_3/data/log.txt"]["body"] == b"new attachment"
    assert client.objects[f"{ROOT}/job_3/index.html"]["ContentType"] == "text/html; charset=utf-8"
    assert client.objects[f"{ROOT}/job_3/app.js"]["ContentType"] in {"text/javascript", "application/javascript"}
    assert client.objects[f"{ROOT}/history/history.json"]["body"] == b'"new trend"'
    assert f"{ROOT}/history/obsolete.json" not in client.objects
    assert not any(f"{ROOT}/job_{number}/" in key for number in (1, 2) for key in client.objects)
    assert b"../job_3/" in client.objects[f"{ROOT}/latest/index.html"]["body"]
    assert b"No server." not in client.objects["archive/index.html"]["body"]
    assert b"Custom reports" in client.objects["archive/index.html"]["body"]
    for key in ("archive/other/main/job_50/index.html", "archive/other/index.html",
                "archive/other/main/index.html", "archive/manual.txt", "outside/keep.bin"):
        assert client.objects[key] == before[key]
        assert ("put", key) not in client.writes
    assert all(key.endswith(("/history/history.json", "/history/obsolete.json",
                             "/widgets/summary.json", "/.modified_at",
                             "/" + gui.NAVIGATION_METADATA_FILENAME)) for key in client.reads)
    recursive = {prefix for prefix, delimiter in client.listings if delimiter is None}
    assert recursive == {f"{ROOT}/{name}/" for name in ("history", "job_1", "job_2", "job_3")}
    assert "archive/" + s3.LOCK_NAME not in client.objects
    assert comments == ["https://reports.test/base/dev/project/browser%20name/job_3/"]
    if provider == "github":
        assert "report-url=" + comments[0] in Path("outputs").read_text()
        assert f"public-dir={Path(args.public_dir).resolve()}" in Path("outputs").read_text()


def test_generation_failure_leaves_remote_reports_history_and_navigation_intact(publication, monkeypatch):
    args, client, _, comments = publication
    before = deepcopy(client.objects)
    def fail_generation(**kwargs):
        raise RuntimeError("generation")

    monkeypatch.setattr(github_adapter, "build_report", fail_generation)
    with pytest.raises(RuntimeError, match="generation"):
        s3.publish_job(args, client)
    assert client.objects == before
    assert not comments


def test_asset_upload_failure_keeps_old_history_and_reports_and_does_not_publish_entry_page(publication):
    args, client, _, comments = publication
    before = deepcopy(client.objects)
    client.fail_key = f"{ROOT}/job_3/data/log.txt"
    with pytest.raises(RuntimeError, match="interrupted upload"):
        s3.publish_job(args, client)
    for key, item in before.items():
        assert client.objects[key] == item
    assert f"{ROOT}/job_3/index.html" not in client.objects
    assert "archive/" + s3.LOCK_NAME not in client.objects
    assert not comments


def test_busy_prefix_refuses_publication(publication):
    args, client, _, _ = publication
    client.add("archive/" + s3.LOCK_NAME, b"another publisher")
    before = deepcopy(client.objects)
    with pytest.raises(ClientError):
        s3.publish_job(args, client)
    assert client.objects == before


def test_full_report_publication_requires_an_owned_prefix(publication):
    args, client, _, _ = publication
    args.s3_uri = "s3://reports"
    with pytest.raises(ValueError, match="dedicated"):
        s3.publish_job(args, client)
    assert not client.writes


def test_existing_report_cannot_be_used_as_the_storage_root(publication):
    args, client, _, _ = publication
    args.s3_uri = f"s3://reports/{ROOT}/job_1"
    before = deepcopy(client.objects)
    with pytest.raises(ValueError, match="navigation root"):
        s3.publish_job(args, client)
    assert client.objects == before


def test_retained_reports_and_unrelated_files_are_never_mirrored_or_pruned(publication, monkeypatch):
    args, client, _, _ = publication
    monkeypatch.setenv("REPORTS_TO_KEEP", "2")
    client.add(f"{ROOT}/job_2/widgets/summary.json", b'{"statistic":{"passed":7,"total":7}}')
    client.add(f"{ROOT}/manual.json", b"keep group metadata")
    before = deepcopy(client.objects)

    s3.publish_job(args, client)

    for key in (f"{ROOT}/job_2/index.html", f"{ROOT}/job_2/data/log.txt", f"{ROOT}/manual.json"):
        assert client.objects[key] == before[key]
        assert ("put", key) not in client.writes
        assert key not in client.reads
    assert (f"{ROOT}/job_2/", None) not in client.listings
    assert b"job_2/" in client.objects[f"{ROOT}/index.html"]["body"]
    assert b".allure-history-navigation.json" not in client.objects[f"{ROOT}/index.html"]["body"]


def test_cached_neighbour_does_not_require_listing_its_large_archive(publication, monkeypatch):
    args, client, restored, _ = publication
    s3.publish_job(args, client)
    for number in range(10_000):
        client.add(f"archive/other/main/job_50/data/attachments/{number}.txt", b"old attachment")
    client.listings.clear()
    client.reads.clear()
    client.writes.clear()
    monkeypatch.setenv("GITHUB_RUN_ID", "4")
    args.public_dir = "second/public"

    s3.publish_job(args, client)

    assert restored == ["previous trend", "new trend"]
    assert not any(prefix.startswith("archive/other/") for prefix, _ in client.listings)
    assert [key for key in client.reads if key.startswith("archive/other/")] == [
        "archive/other/" + gui.NAVIGATION_METADATA_FILENAME]
    assert not any(key.startswith("archive/other/") for _, key in client.writes)
    assert client.objects["archive/other/main/index.html"]["body"] == b"old neighbour group GUI"
    assert b'href="other/"' in client.objects["archive/index.html"]["body"]
    assert b"../job_4/" in client.objects[f"{ROOT}/latest/index.html"]["body"]


def test_same_id_retry_removes_only_obsolete_files_in_that_snapshot(publication, monkeypatch):
    args, client, _, _ = publication
    monkeypatch.setenv("REPORTS_TO_KEEP", "2")
    client.add(f"{ROOT}/job_3/data/obsolete.txt", b"partial failed upload")
    client.add(f"{ROOT}/job_30/data/keep.txt", b"a newer report")

    s3.publish_job(args, client)

    assert f"{ROOT}/job_3/data/obsolete.txt" not in client.objects
    assert f"{ROOT}/job_3/index.html" in client.objects
    assert client.objects[f"{ROOT}/job_30/data/keep.txt"]["body"] == b"a newer report"


def test_cached_sibling_keeps_its_latest_link_in_the_shared_parent(publication, monkeypatch):
    args, client, _, _ = publication
    sibling = "archive/dev/project/other-group"
    client.add(sibling + "/job_99/index.html", b"neighbour report")
    client.add(sibling + "/index.html", b"neighbour GUI")
    s3.publish_job(args, client)
    client.listings.clear()
    monkeypatch.setenv("GITHUB_RUN_ID", "4")
    args.public_dir = "second/public"

    s3.publish_job(args, client)

    assert b'href="other-group/latest/"' in client.objects["archive/dev/project/index.html"]["body"]
    assert client.objects[sibling + "/index.html"]["body"] == b"neighbour GUI"
    assert not any(prefix.startswith(sibling + "/") for prefix, _ in client.listings)


def test_new_custom_group_is_created_without_touching_old_groups(publication, monkeypatch):
    args, client, restored, _ = publication
    group = "teams/QA +/проверка"
    monkeypatch.setenv("ALLURE_HISTORY_GROUP_PATH", group)
    before = deepcopy(client.objects)

    s3.publish_job(args, client)

    assert restored == [None]
    assert f"archive/{group}/job_3/index.html" in client.objects
    assert "archive/teams/QA +/index.html" in client.objects
    assert b"QA%20%2B/" in client.objects["archive/teams/index.html"]["body"]
    for key, item in before.items():
        assert client.objects[key] == item
    assert not any(prefix.startswith(ROOT) and delimiter is None for prefix, delimiter in client.listings)


@pytest.mark.parametrize("key", [f"{ROOT}/history/history.json", "archive/index.html",
                                  f"{ROOT}/job_2/widgets/summary.json"])
def test_changed_sources_are_rejected_before_report_upload(publication, monkeypatch, key):
    args, client, _, comments = publication
    monkeypatch.setenv("REPORTS_TO_KEEP", "2")
    client.add(f"{ROOT}/job_2/widgets/summary.json", b"{}")
    build = github_adapter.build_report

    def racing_build(**kwargs):
        build(**kwargs)
        client.add(key, b"changed during generation")

    monkeypatch.setattr(github_adapter, "build_report", racing_build)
    with pytest.raises((RuntimeError, ClientError)):
        s3.publish_job(args, client)
    assert client.objects[key]["body"] == b"changed during generation"
    assert all(key.endswith(s3.LOCK_NAME) for _, key in client.writes)
    assert not comments


@pytest.mark.parametrize("metadata", [
    {"version": 99},
    {"version": 1, "latest": {"path": "../../escape", "modified_at": ""}},
])
def test_invalid_cached_paths_or_versions_cannot_modify_reports(publication, metadata):
    args, client, _, _ = publication
    client.add("archive/other/" + gui.NAVIGATION_METADATA_FILENAME, json.dumps(metadata).encode())
    before = deepcopy(client.objects)
    with pytest.raises(ValueError):
        s3.publish_job(args, client)
    assert client.objects == before
    assert not Path("escape").exists()


def test_cache_upload_failure_never_prunes_previous_reports(publication):
    args, client, _, _ = publication
    client.fail_key = f"{ROOT}/" + gui.NAVIGATION_METADATA_FILENAME
    with pytest.raises(RuntimeError, match="interrupted upload"):
        s3.publish_job(args, client)
    assert all(f"{ROOT}/job_{number}/index.html" in client.objects for number in (1, 2))


def test_folder_markers_are_not_downloaded_and_expired_report_markers_are_removed(publication):
    args, client, restored, _ = publication
    for key in (f"{ROOT}/job_1/", f"{ROOT}/job_1/data/", f"{ROOT}/history/"):
        client.add(key, b"")
    client.add("archive/other/", b"")

    s3.publish_job(args, client)

    assert restored == ["previous trend"]
    assert not any(key.endswith("/") for key in client.reads)
    assert not any(key.startswith(f"{ROOT}/job_1/") for key in client.objects)
    assert "archive/other/" in client.objects


def test_global_gui_refresh_is_explicit_and_can_be_rolled_back(publication, monkeypatch):
    args, client, _, _ = publication
    before = deepcopy(client.objects)
    import boto3
    monkeypatch.setattr(boto3, "client", lambda service: client)

    assert s3.main(["refresh-gui", "--s3-uri", args.s3_uri, "--public-url", args.public_url,
                    "--output-dir", "gui-refresh"]) == 0

    assert client.objects["archive/other/main/index.html"]["body"] != b"old neighbour group GUI"
    cache = "archive/other/" + gui.NAVIGATION_METADATA_FILENAME
    assert json.loads(client.objects[cache]["body"])["latest"]["path"] == "main/job_50"
    assert client.objects[cache]["ContentType"] == "application/json"
    for key, item in before.items():
        if "/job_" in key or "/history/" in key:
            assert client.objects[key] == item
    s3.apply_plan(Path("gui-refresh/plan.json").resolve(), client, rollback=True)
    assert client.objects == before
