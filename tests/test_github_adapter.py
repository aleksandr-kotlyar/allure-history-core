from pathlib import Path
from types import SimpleNamespace

import pytest

import github_adapter


def test_build_report_uses_github_environment_contract(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(github_adapter, "run_command", fake_run)
    monkeypatch.setattr(github_adapter, "validate_runtime_commands", lambda: None)
    monkeypatch.setattr(github_adapter, "utc_timestamp", lambda: "2026-06-14T09:00:00Z")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REF_NAME", "Feature/Login")
    monkeypatch.setenv("GITHUB_RUN_ID", "456")
    monkeypatch.setenv("REPORTS_TO_KEEP", "30")
    monkeypatch.setenv(
        "GITHUB_WORKFLOW_REF",
        "aleksandr-kotlyar/allure-history-github/.github/workflows/allure-history.yml@2026.2.11",
    )
    jobid_dir = tmp_path / ".artifacts" / "jobid"
    jobid_dir.mkdir(parents=True)
    (jobid_dir / "jobid").write_text("123\n", encoding="utf-8")
    results_dir = tmp_path / "allure-results"
    results_dir.mkdir()
    (results_dir / "result.json").write_text("{}\n", encoding="utf-8")

    github_adapter.build_report()

    assert commands == [
        [
            "allure-history",
            "build-report",
            "--results-dir",
            "allure-results",
            "--public-dir",
            "pages-worktree/public",
            "--environment",
            "dev",
            "--branch",
            "feature-login",
            "--report-id",
            "123",
            "--pages-base-url",
            "https://example.github.io/project",
            "--ci-name",
            "GitHub Actions",
            "--ci-type",
            "github",
            "--build-url",
            "https://github.com/example/project/actions/runs/456",
            "--build-name",
            "GitHub Actions Run 123",
            "--modified-at",
            "2026-06-14T09:00:00Z",
            "--reports-to-keep",
            "30",
            "--public-title",
            "allure-history-github",
            "--publisher-title",
            "Allure History GitHub",
            "--pages-name",
            "GitHub Pages",
            "--generator-name",
            "allure-history-github",
            "--generator-url",
            "https://github.com/aleksandr-kotlyar/allure-history-github",
            "--generator-version",
            "2026.2.11",
            "--output-file",
            "allure-history-output.json",
            "--generator-version-url",
            "https://github.com/aleksandr-kotlyar/allure-history-github/releases/tag/2026.2.11",
        ]
    ]


def test_default_pages_base_url_handles_project_and_user_pages(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    assert github_adapter.default_pages_base_url() == "https://example.github.io/project"

    monkeypatch.setenv("GITHUB_REPOSITORY", "example/example.github.io")
    assert github_adapter.default_pages_base_url() == "https://example.github.io"


def test_generator_version_prefers_external_action_ref(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTION_REF", "2026.5.0")
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", "consumer/project/.github/workflows/test.yml@main")

    assert github_adapter.generator_version() == "2026.5.0"


def test_job_id_prefers_action_input_over_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    jobid = tmp_path / ".artifacts" / "jobid" / "jobid"
    jobid.parent.mkdir(parents=True)
    jobid.write_text("123\n")
    monkeypatch.setenv("GITHUB_RUN_ID", "456")
    monkeypatch.setenv("ALLURE_HISTORY_JOB_ID", "789")

    assert github_adapter.job_id() == "789"


def test_publish_job_exports_fork_report_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/fork")
    monkeypatch.setenv("GITHUB_REF_NAME", "Feature/Login")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    for name in (
        "validate_runtime_commands", "ensure_results_present", "prepare_pages_worktree",
        "build_report", "publish_pages_worktree", "comment_pr",
    ):
        monkeypatch.setattr(github_adapter, name, lambda: None)

    github_adapter.publish_job()

    assert (tmp_path / "outputs").read_text() == (
        "report-url=https://someone.github.io/fork/dev/feature-login/job_123/\n"
        "public-dir=public\n"
    )


def test_publish_job_rejects_multiline_output_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setattr(github_adapter, "report_url", lambda: "https://example/\ninjected=value")
    for name in (
        "validate_runtime_commands", "ensure_results_present", "prepare_pages_worktree",
        "build_report", "publish_pages_worktree", "comment_pr",
    ):
        monkeypatch.setattr(github_adapter, name, lambda: None)

    with pytest.raises(RuntimeError, match="single line"):
        github_adapter.publish_job()

    assert not (tmp_path / "outputs").exists()


def test_publish_job_checks_results_before_accessing_remote(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(github_adapter, "validate_runtime_commands", lambda: None)

    def unexpected_remote():
        raise AssertionError("Remote access must not run without results")

    monkeypatch.setattr(github_adapter, "prepare_pages_worktree", unexpected_remote)

    assert github_adapter.main(["publish-job"]) == 1


def run_prepare_pages_worktree(tmp_path, monkeypatch, branch_status):
    commands: list[str] = []

    def fake_git(*args, cwd=None, check=True):
        del cwd, check
        commands.append(" ".join(args))
        if args[:3] == ("ls-remote", "--exit-code", "--heads"):
            return SimpleNamespace(returncode=branch_status)
        return SimpleNamespace(returncode=0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("PAGES_BRANCH", "gh-pages")
    monkeypatch.setattr(github_adapter, "git", fake_git)

    result = github_adapter.main(["prepare-pages-worktree"])
    return result, commands


def test_prepare_pages_worktree_clones_existing_storage_branch(tmp_path, monkeypatch):
    result, commands = run_prepare_pages_worktree(
        tmp_path,
        monkeypatch,
        branch_status=0,
    )

    assert result == 0
    assert (
        "clone --single-branch --branch gh-pages "
        "https://x-access-token:secret@github.com/example/project.git pages-worktree"
    ) in commands
    assert not any(command.startswith("init ") for command in commands)


def test_prepare_pages_worktree_creates_missing_storage_branch(tmp_path, monkeypatch):
    result, commands = run_prepare_pages_worktree(
        tmp_path,
        monkeypatch,
        branch_status=2,
    )

    assert result == 0
    assert "init pages-worktree" in commands
    assert (
        "-C pages-worktree remote add origin "
        "https://x-access-token:secret@github.com/example/project.git"
    ) in commands
    assert "-C pages-worktree checkout --orphan gh-pages" in commands
    assert not any(command.startswith("clone ") for command in commands)


def run_publish_pages_worktree(
    tmp_path,
    monkeypatch,
    successful_push,
    remote_branch_exists=True,
):
    calls: list[str] = []
    commands: list[str] = []
    push_count = 0

    def fake_git(*args, cwd=None, check=True):
        nonlocal push_count
        del cwd, check
        commands.append(" ".join(args))

        if len(args) >= 3 and args[2] == "diff":
            return SimpleNamespace(returncode=1)
        if len(args) >= 3 and args[2] == "ls-remote":
            return SimpleNamespace(returncode=0 if remote_branch_exists else 2)
        if len(args) >= 3 and args[2] == "pull":
            calls.append("pull")
            return SimpleNamespace(returncode=0)
        if len(args) >= 3 and args[2] == "push":
            calls.append("push")
            push_count += 1
            return SimpleNamespace(
                returncode=0
                if successful_push > 0 and push_count >= successful_push
                else 1
            )

        return SimpleNamespace(returncode=0)

    def fake_run(command, **kwargs):
        del kwargs
        if command == ["sleep", "2"]:
            return SimpleNamespace(returncode=0)
        raise AssertionError(f"Unexpected run_command call: {command}")

    pages_public = tmp_path / "pages-worktree" / "public"
    pages_public.mkdir(parents=True)
    (pages_public / "index.html").write_text("report", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("PAGES_BRANCH", "gh-pages")
    monkeypatch.setattr(github_adapter, "git", fake_git)
    monkeypatch.setattr(github_adapter, "run_command", fake_run)

    result = github_adapter.main(["publish-pages-worktree"])
    return result, calls, commands


def test_publish_pages_worktree_retries_after_racing_updates(tmp_path, monkeypatch):
    result, calls, commands = run_publish_pages_worktree(
        tmp_path,
        monkeypatch,
        successful_push=3,
    )

    assert result == 0
    assert calls == [
        "pull",
        "push",
        "pull",
        "push",
        "pull",
        "push",
    ]
    assert (tmp_path / "public" / "index.html").read_text(encoding="utf-8") == "report"
    assert any(
        command.endswith("commit -m Publish Allure report for run 123 [skip ci]")
        for command in commands
    )
    assert sum(command.endswith("push origin HEAD:gh-pages") for command in commands) == 3


def test_publish_pages_worktree_skips_pull_for_first_push(tmp_path, monkeypatch):
    result, calls, _ = run_publish_pages_worktree(
        tmp_path,
        monkeypatch,
        successful_push=1,
        remote_branch_exists=False,
    )

    assert result == 0
    assert calls == ["push"]
    assert (tmp_path / "public" / "index.html").is_file()


def test_render_comment_body_contains_marker_and_summary():
    body = github_adapter.render_comment_body(
        "https://example.github.io/project/dev/branch/job_123/",
        {"passed": 42, "failed": 1, "broken": 1, "skipped": 0, "unknown": 0, "total": 44},
    )

    assert github_adapter.PR_MARKER in body
    assert "**FAILED**" in body
    assert "failed 1, broken 1" in body
    assert "#categories" in body


def test_comment_pr_updates_existing_marker_comment(tmp_path, monkeypatch):
    requests: list[tuple[str, str, dict[str, str] | None]] = []

    def fake_api_request(method, url, token, *, body=None):
        assert token == "secret"
        requests.append((method, url, body))
        if method == "GET":
            return 200, [{"id": 123, "body": github_adapter.PR_MARKER}]
        if method == "PATCH":
            return 200, {}
        raise AssertionError(f"Unexpected API request: {method} {url}")

    snapshot = tmp_path / "snapshot"
    widgets = snapshot / "widgets"
    widgets.mkdir(parents=True)
    (widgets / "summary.json").write_text(
        '{"statistic":{"passed":1,"failed":0,"broken":0,"skipped":0,"unknown":0,"total":1}}\n',
        encoding="utf-8",
    )
    (tmp_path / "allure-history-output.json").write_text(
        '{"report_url":"https://example.github.io/project/dev/branch/job_123/","snapshot_dir":"'
        + str(snapshot)
        + '"}\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALLURE_HISTORY_COMMENT_PR", "true")
    monkeypatch.setenv("GITHUB_PR_NUMBER", "7")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.setattr(github_adapter, "github_api_request", fake_api_request)

    github_adapter.comment_pr()

    assert requests == [
        (
            "GET",
            "https://api.github.test/repos/example/project/issues/7/comments?per_page=100",
            None,
        ),
        (
            "PATCH",
            "https://api.github.test/repos/example/project/issues/comments/123",
            {
                "body": (
                    f"{github_adapter.PR_MARKER}\n"
                    "> [!TIP]\n"
                    "> **PASSED** - [Allure report]"
                    "(https://example.github.io/project/dev/branch/job_123/#categories) - 1 total"
                )
            },
        ),
    ]


def test_comment_pr_finds_marker_beyond_first_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALLURE_HISTORY_COMMENT_PR", "true")
    monkeypatch.setenv("GITHUB_PR_NUMBER", "7")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.setattr(github_adapter, "report_url", lambda: "https://example.github.io/project/report/")
    monkeypatch.setattr(github_adapter, "summary_path", lambda: Path("absent.json"))
    calls = []

    def fake_api(method, url, token, *, body=None):
        calls.append((method, url))
        if method == "PATCH":
            return 200, {}
        if "&page=2" in url:
            return 200, [{"id": 456, "body": github_adapter.PR_MARKER}]
        return 200, [{"id": n, "body": "other comment"} for n in range(100)]

    monkeypatch.setattr(github_adapter, "github_api_request", fake_api)

    github_adapter.comment_pr()

    assert len(calls) == 3
    assert calls[1][1].endswith("?per_page=100&page=2")
    assert calls[2] == ("PATCH", "https://api.github.com/repos/example/project/issues/comments/456")


def test_comment_pr_reports_creation_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALLURE_HISTORY_COMMENT_PR", "true")
    monkeypatch.setenv("GITHUB_PR_NUMBER", "7")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.setattr(github_adapter, "report_url", lambda: "https://example.github.io/project/report/")
    monkeypatch.setattr(github_adapter, "summary_path", lambda: Path("absent.json"))
    monkeypatch.setattr(github_adapter, "github_api_request", lambda method, *args, **kwargs: (200, []) if method == "GET" else (403, {}))

    assert github_adapter.main(["comment-pr"]) == 1
