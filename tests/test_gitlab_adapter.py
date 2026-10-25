from pathlib import Path
from types import SimpleNamespace

import gitlab_adapter


def test_build_report_uses_gitlab_environment_contract(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gitlab_adapter, "run_command", fake_run)
    monkeypatch.setattr(gitlab_adapter, "validate_runtime_commands", lambda: None)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CI_COMMIT_REF_SLUG", "feature-login")
    monkeypatch.setenv("CI_PAGES_URL", "https://example.gitlab.io/project")
    monkeypatch.setenv("CI_PIPELINE_URL", "https://gitlab.example/group/project/-/pipelines/123")
    monkeypatch.setenv("CI_PIPELINE_CREATED_AT", "2026-06-14T09:00:00Z")
    monkeypatch.setenv("REPORTS_TO_KEEP", "30")
    monkeypatch.setenv("COMPONENT_VERSION", "2026.2.9")
    (tmp_path / "jobid").write_text("123\n", encoding="utf-8")
    results_dir = tmp_path / "allure-results"
    results_dir.mkdir()
    (results_dir / "result.json").write_text("{}\n", encoding="utf-8")

    gitlab_adapter.build_report()

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
            "https://example.gitlab.io/project",
            "--ci-name",
            "GitLab CI",
            "--ci-type",
            "gitlab",
            "--build-url",
            "https://gitlab.example/group/project/-/pipelines/123",
            "--build-name",
            "GitLab Job Run 123",
            "--modified-at",
            "2026-06-14T09:00:00Z",
            "--reports-to-keep",
            "30",
            "--public-title",
            "allure-history-gitlab",
            "--publisher-title",
            "Allure History GitLab",
            "--pages-name",
            "GitLab Pages",
            "--generator-name",
            "allure-history-gitlab",
            "--generator-url",
            "https://gitlab.com/aleksandr-kotlyar/allure-history-gitlab",
            "--generator-version",
            "2026.2.9",
            "--generator-version-url",
            "https://gitlab.com/explore/catalog/aleksandr-kotlyar/allure-history-gitlab",
            "--output-file",
            "allure-history-output.json",
        ]
    ]


def test_build_report_prefers_generator_version_override(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gitlab_adapter, "run_command", fake_run)
    monkeypatch.setattr(gitlab_adapter, "validate_runtime_commands", lambda: None)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CI_COMMIT_REF_SLUG", "feature-login")
    monkeypatch.setenv("CI_PAGES_URL", "https://example.gitlab.io/project")
    monkeypatch.setenv("CI_PIPELINE_URL", "https://gitlab.example/group/project/-/pipelines/123")
    monkeypatch.setenv("ALLURE_HISTORY_GENERATOR_VERSION", "2026.2.10")
    monkeypatch.setenv("ALLURE_HISTORY_IMAGE_TAG", "2026.2.9")
    (tmp_path / "jobid").write_text("123\n", encoding="utf-8")
    results_dir = tmp_path / "allure-results"
    results_dir.mkdir()
    (results_dir / "result.json").write_text("{}\n", encoding="utf-8")

    gitlab_adapter.build_report()

    assert commands[0][commands[0].index("--generator-version") + 1] == "2026.2.10"


def test_build_report_copies_combined_dependency_artifacts(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gitlab_adapter, "run_command", fake_run)
    monkeypatch.setattr(gitlab_adapter, "validate_runtime_commands", lambda: None)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CI_COMMIT_REF_SLUG", "feature-login")
    monkeypatch.setenv("CI_PAGES_URL", "https://example.gitlab.io/project")
    monkeypatch.setenv("CI_PIPELINE_URL", "https://gitlab.example/group/project/-/pipelines/123")
    (tmp_path / "jobid").write_text("123\n", encoding="utf-8")
    dependency_results = tmp_path / "test_gate" / "allure-results"
    dependency_results.mkdir(parents=True)
    (dependency_results / "result.json").write_text("{}\n", encoding="utf-8")

    gitlab_adapter.build_report()

    assert (tmp_path / "allure-results" / "result.json").is_file()
    assert commands[0][commands[0].index("--results-dir") + 1] == "allure-results"


def test_build_report_requires_results_when_no_artifacts_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gitlab_adapter, "validate_runtime_commands", lambda: None)

    result = gitlab_adapter.main(["build-report"])

    assert result == 1


def test_publish_job_rejects_empty_configured_image_tag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALLURE_HISTORY_IMAGE_TAG", "")
    monkeypatch.setattr(gitlab_adapter, "ensure_git_available", lambda: None)
    monkeypatch.setattr(gitlab_adapter, "validate_runtime_commands", lambda: None)

    result = gitlab_adapter.main(["publish-job"])

    assert result == 1


def run_prepare_pages_worktree(tmp_path, monkeypatch, branch_status):
    commands: list[str] = []

    def fake_git(*args, cwd=None, check=True):
        del cwd, check
        commands.append(" ".join(args))
        if args[:3] == ("ls-remote", "--exit-code", "--heads"):
            return SimpleNamespace(returncode=branch_status)
        return SimpleNamespace(returncode=0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CI_REPOSITORY_URL", "https://gitlab.example/group/project.git")
    monkeypatch.setenv("PAGES_BRANCH", "gl-pages")
    monkeypatch.setattr(gitlab_adapter, "ensure_git_available", lambda: None)
    monkeypatch.setattr(gitlab_adapter, "git", fake_git)

    result = gitlab_adapter.main(["prepare-pages-worktree"])
    return result, commands


def test_prepare_pages_worktree_clones_existing_storage_branch(tmp_path, monkeypatch):
    result, commands = run_prepare_pages_worktree(
        tmp_path,
        monkeypatch,
        branch_status=0,
    )

    assert result == 0
    assert (
        "clone --single-branch --branch gl-pages "
        "https://gitlab.example/group/project.git pages-worktree"
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
        "https://gitlab.example/group/project.git"
    ) in commands
    assert "-C pages-worktree checkout --orphan gl-pages" in commands
    assert not any(command.startswith("clone ") for command in commands)


def test_prepare_pages_worktree_reports_branch_check_failures(tmp_path, monkeypatch):
    result, commands = run_prepare_pages_worktree(
        tmp_path,
        monkeypatch,
        branch_status=128,
    )

    assert result == 1
    assert not any(command.startswith(("clone ", "init ")) for command in commands)


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
            return SimpleNamespace(returncode=0 if successful_push > 0 and push_count >= successful_push else 1)

        return SimpleNamespace(returncode=0)

    def fake_run(command, **kwargs):
        del kwargs
        if command == ["sleep", "2"]:
            return SimpleNamespace(returncode=0)
        raise AssertionError(f"Unexpected run_command call: {command}")

    pages_public = tmp_path / "pages-worktree" / "public"
    pages_public.mkdir(parents=True)
    (pages_public / "index.html").write_text("report", encoding="utf-8")
    (tmp_path / "pages-worktree" / ".gitlab-ci.yml").write_text(
        "pages:\n  script: echo legacy\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_PUSH_TOKEN", "secret")
    monkeypatch.setenv("CI_SERVER_HOST", "gitlab.example")
    monkeypatch.setenv("CI_PROJECT_PATH", "group/project")
    monkeypatch.setenv("CI_PIPELINE_ID", "123")
    monkeypatch.setenv("PAGES_BRANCH", "gl-pages")
    monkeypatch.setattr(gitlab_adapter, "git", fake_git)
    monkeypatch.setattr(gitlab_adapter, "run_command", fake_run)

    result = gitlab_adapter.main(["publish-pages-worktree"])
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
    assert not (tmp_path / "pages-worktree" / ".gitlab-ci.yml").exists()

    assert any(
        command.endswith(
            "commit -m Publish Allure report for pipeline 123 [skip ci]"
        )
        for command in commands
    )
    assert sum(
        command.endswith("push -o ci.skip origin HEAD:gl-pages")
        for command in commands
    ) == 3


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


def test_publish_pages_worktree_fails_after_three_attempts(tmp_path, monkeypatch):
    result, calls, _ = run_publish_pages_worktree(
        tmp_path,
        monkeypatch,
        successful_push=0,
    )

    assert result == 1
    assert calls.count("push") == 3
    assert not (tmp_path / "public").exists()


def test_render_comment_body_uses_issue_summary_for_failed_reports():
    body = gitlab_adapter.render_comment_body(
        "https://example.gitlab.io/project/dev/branch/job_123/",
        {"passed": 42, "failed": 1, "broken": 1, "skipped": 0, "unknown": 0, "total": 44},
    )

    assert body == (
        "> [!caution]\n"
        "> \U0001f534 **FAILED** \u00b7 "
        "[Allure report 2 issues]"
        "(https://example.gitlab.io/project/dev/branch/job_123/#categories) "
        "(failed 1, broken 1) \u00b7 44 total"
    )


def test_render_comment_body_uses_tip_alert_for_passed_reports():
    body = gitlab_adapter.render_comment_body(
        "https://example.gitlab.io/project/dev/branch/job_123/",
        {"passed": 44, "failed": 0, "broken": 0, "skipped": 0, "unknown": 0, "total": 44},
    )

    assert body == (
        "> [!tip]\n"
        "> \U0001f7e2 **PASSED** \u00b7 "
        "[Allure report]"
        "(https://example.gitlab.io/project/dev/branch/job_123/#categories) "
        "\u00b7 44 total"
    )


def test_find_existing_note_supports_current_and_legacy_markers():
    notes = [
        {
            "id": 123,
            "body": "Allure report\n\n<!-- allure-history-report -->",
            "system": False,
            "author": {"id": 42},
        }
    ]

    assert gitlab_adapter.find_existing_note(notes, "42") == "OWN:123"


def test_find_existing_note_supports_current_report_link_marker():
    notes = [
        {
            "id": 123,
            "body": "> [!tip]\n> \U0001f7e2 **PASSED** \u00b7 [Allure report](https://example.test/#categories)",
            "system": False,
            "author": {"id": 42},
        }
    ]

    assert gitlab_adapter.find_existing_note(notes, "42") == "OWN:123"
