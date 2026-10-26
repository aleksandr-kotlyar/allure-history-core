import argparse
from copy import deepcopy
import json
from pathlib import Path

from botocore.exceptions import ClientError
import pytest

import allure_history
import github_adapter
import gitlab_adapter
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
        restored.append(json.loads((results / "history/history.json").read_text()))
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
    for key in ("archive/other/main/job_50/index.html", "archive/manual.txt", "outside/keep.bin"):
        assert client.objects[key] == before[key]
        assert ("put", key) not in client.writes
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
