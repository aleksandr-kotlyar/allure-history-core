import json
from pathlib import Path

import pytest

import allure_history
import github_adapter
import gitlab_adapter
from report_paths import report_group_path
from tests.test_public_tree_links import find_local_href_targets, target_exists_in_public_tree


def build_args(tmp_path, group, report_id):
    return [
        "build-report", "--results-dir", str(tmp_path / "results"),
        "--public-dir", str(tmp_path / "public"), "--work-dir", str(tmp_path),
        "--group-path", group, "--report-id", report_id,
        "--pages-base-url", "https://reports.test/prefix",
        "--ci-name", "CI", "--ci-type", "ci", "--build-url", "https://ci.test/1",
        "--build-name", "Run", "--reports-to-keep", "1",
        "--output-file", str(tmp_path / "output.json"),
    ]


def test_custom_groups_restore_their_own_history_and_prune_only_their_snapshots(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    (results / "result.json").write_text("{}")
    restored = []

    def generate(command, **kwargs):
        history = results / "history" / "history.json"
        restored.append(json.loads(history.read_text()) if history.exists() else None)
        executor = json.loads((results / "executor.json").read_text())
        output = Path(command[4])
        (output / "history").mkdir(parents=True)
        (output / "widgets").mkdir()
        (output / "index.html").write_text("<html>report</html>")
        (output / "history" / "history.json").write_text(json.dumps(executor["buildOrder"]))
        (output / "widgets" / "summary.json").write_text('{"statistic":{"passed":1,"total":1}}')

    monkeypatch.setattr(allure_history.subprocess, "run", generate)
    first = "dev/qa python/qa-mdsp-latest"
    other = "dev/qa python/other"
    for group, report_id in [(first, "101"), (other, "201"), (first, "102")]:
        assert allure_history.main(build_args(tmp_path, group, report_id)) == 0

    assert restored == [None, None, "101"]
    public = tmp_path / "public"
    group = public / first
    assert not (group / "job_101").exists()
    assert (group / "job_102" / "index.html").is_file()
    assert (public / other / "job_201" / "index.html").is_file()
    assert json.loads((group / "history" / "history.json").read_text()) == "102"
    assert json.loads((public / other / "history" / "history.json").read_text()) == "201"
    assert "../job_102/" in (group / "latest" / "index.html").read_text()
    assert (public / "dev" / ".modified_at").is_file()

    output = json.loads((tmp_path / "output.json").read_text())
    assert output["group_path"] == first
    assert output["snapshot_dir"] == str(group / "job_102")
    assert output["report_url"] == "https://reports.test/prefix/dev/qa%20python/qa-mdsp-latest/job_102/"
    assert json.loads((results / "executor.json").read_text())["reportUrl"] == output["report_url"]
    references = find_local_href_targets(public)
    assert references
    assert all(target_exists_in_public_tree(public, target) for _, _, target in references)


@pytest.mark.parametrize("value", [
    "/absolute", "../outside", "team/../outside", "team/./branch", "team//branch", "team/",
    "team\\branch", "team/\nbranch", "team/\x00branch", "team/\x7fbranch",
    "team/history", "team/.history", "team/latest", "team/index.html", "team/.modified_at",
    "team/data", "team/widgets", "team/job_123", "team/job_custom", "team/2026-10-26_09-00-00",
])
def test_group_path_rejects_ambiguous_or_reserved_segments(value):
    with pytest.raises(ValueError):
        report_group_path(value, "dev", "main")


def test_group_path_uses_ci_values_literally_without_a_template_language():
    assert report_group_path("проект/Chrome & Firefox", "", "") == Path("проект/Chrome & Firefox")
    assert report_group_path("reports", "ignored", "ignored") == Path("reports")
    assert report_group_path("$PROJECT/$BRANCH", "", "") == Path("$PROJECT/$BRANCH")
    assert report_group_path("", "dev", "main") == Path("dev/main")


@pytest.mark.parametrize("kind", ["report", "symlink", "file"])
def test_build_rejects_conflicting_group_before_changing_results_or_storage(tmp_path, monkeypatch, kind):
    results = tmp_path / "results"
    results.mkdir()
    (results / "result.json").write_text("original results")
    public = tmp_path / "public"
    public.mkdir()
    conflict = public / "existing"
    if kind == "file":
        conflict.write_text("original file")
    elif kind == "symlink":
        conflict.symlink_to(results, target_is_directory=True)
    else:
        (conflict / "widgets").mkdir(parents=True)
        (conflict / "index.html").write_text("original report")
    monkeypatch.setattr(allure_history.subprocess, "run", lambda *a, **k: pytest.fail("Allure invoked"))

    assert allure_history.main(build_args(tmp_path, "existing/child", "1")) == 1
    assert not (results / "executor.json").exists()
    assert not (results / "child").exists()
    assert (results / "result.json").read_text() == "original results"
    if kind == "report":
        assert (conflict / "index.html").read_text() == "original report"


@pytest.mark.parametrize("adapter", [gitlab_adapter, github_adapter])
def test_adapters_pass_group_path_and_use_it_in_report_links(tmp_path, monkeypatch, adapter):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "allure-results").mkdir()
    (tmp_path / "allure-results" / "result.json").write_text("{}")
    monkeypatch.setenv("ALLURE_HISTORY_GROUP_PATH", "dev/project/browser name")
    monkeypatch.setenv("CI_JOB_ID", "123")
    monkeypatch.setenv("CI_PAGES_URL", "https://reports.test/prefix")
    monkeypatch.setenv("CI_PIPELINE_URL", "https://ci.test/1")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/project")
    monkeypatch.setenv("PAGES_BASE_URL_INPUT", "https://reports.test/prefix")
    monkeypatch.setattr(adapter, "validate_runtime_commands", lambda: None)
    commands = []
    monkeypatch.setattr(adapter, "run_command", lambda command, **kwargs: commands.append(command))

    adapter.build_report()

    assert commands[0][-2:] == ["--group-path", "dev/project/browser name"]
    assert adapter.report_url() == "https://reports.test/prefix/dev/project/browser%20name/job_123/"
    if adapter is github_adapter:
        assert adapter.summary_path() == Path("pages-worktree/public/dev/project/browser name/job_123/widgets/summary.json")
