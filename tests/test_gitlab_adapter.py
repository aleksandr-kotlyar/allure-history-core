import textwrap
from pathlib import Path

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
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CI_COMMIT_REF_SLUG", "feature-login")
    monkeypatch.setenv("CI_PAGES_URL", "https://example.gitlab.io/project")
    monkeypatch.setenv("CI_PIPELINE_URL", "https://gitlab.example/group/project/-/pipelines/123")
    monkeypatch.setenv("CI_PIPELINE_CREATED_AT", "2026-06-14T09:00:00Z")
    monkeypatch.setenv("REPORTS_TO_KEEP", "30")
    monkeypatch.setenv("COMPONENT_VERSION", "2026.2.9")
    (tmp_path / "jobid").write_text("123\n", encoding="utf-8")

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
            "gitlab-allure-history",
            "--publisher-title",
            "GitLab Allure History Publisher",
            "--pages-name",
            "GitLab Pages",
            "--generator-name",
            "gitlab-allure-history",
            "--generator-url",
            "https://gitlab.com/aleksandr-kotlyar/gitlab-allure-history",
            "--generator-version",
            "2026.2.9",
            "--output-file",
            "allure-history-output.json",
        ]
    ]


def run_prepare_pages_worktree(tmp_path, monkeypatch, branch_status):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            printf '%s\n' "$*" >> "$GIT_COMMANDS"

            if [ "$1" = "ls-remote" ]; then
              exit "$BRANCH_STATUS"
            fi

            if [ "$1" = "init" ]; then
              mkdir -p "$2"
            fi
            """
        ),
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    commands_file = tmp_path / "git-commands"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")
    monkeypatch.setenv("GIT_COMMANDS", str(commands_file))
    monkeypatch.setenv("BRANCH_STATUS", str(branch_status))
    monkeypatch.setenv("CI_REPOSITORY_URL", "https://gitlab.example/group/project.git")
    monkeypatch.setenv("PAGES_BRANCH", "gl-pages")

    result = gitlab_adapter.main(["prepare-pages-worktree"])
    commands = commands_file.read_text(encoding="utf-8").splitlines()
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
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            printf '%s\n' "$*" >> "$GIT_COMMANDS"
            command="$3"

            case "$command" in
              diff)
                exit 1
                ;;
              ls-remote)
                [ "$REMOTE_BRANCH_EXISTS" = "true" ] || exit 2
                ;;
              pull)
                printf 'pull\n' >> "$GIT_CALLS"
                ;;
              push)
                printf 'push\n' >> "$GIT_CALLS"
                push_count=$(grep -c '^push$' "$GIT_CALLS")
                [ "$SUCCESSFUL_PUSH" -gt 0 ] && [ "$push_count" -ge "$SUCCESSFUL_PUSH" ]
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)

    pages_public = tmp_path / "pages-worktree" / "public"
    pages_public.mkdir(parents=True)
    (pages_public / "index.html").write_text("report", encoding="utf-8")
    (tmp_path / "pages-worktree" / ".gitlab-ci.yml").write_text(
        "pages:\n  script: echo legacy\n",
        encoding="utf-8",
    )
    calls_file = tmp_path / "git-calls"
    commands_file = tmp_path / "git-commands"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")
    monkeypatch.setenv("GIT_CALLS", str(calls_file))
    monkeypatch.setenv("GIT_COMMANDS", str(commands_file))
    monkeypatch.setenv("SUCCESSFUL_PUSH", str(successful_push))
    monkeypatch.setenv(
        "REMOTE_BRANCH_EXISTS",
        "true" if remote_branch_exists else "false",
    )
    monkeypatch.setenv("GIT_PUSH_TOKEN", "secret")
    monkeypatch.setenv("CI_SERVER_HOST", "gitlab.example")
    monkeypatch.setenv("CI_PROJECT_PATH", "group/project")
    monkeypatch.setenv("CI_PIPELINE_ID", "123")
    monkeypatch.setenv("PAGES_BRANCH", "gl-pages")

    result = gitlab_adapter.main(["publish-pages-worktree"])
    return result, calls_file, commands_file


def test_publish_pages_worktree_retries_after_racing_updates(tmp_path, monkeypatch):
    result, calls_file, commands_file = run_publish_pages_worktree(
        tmp_path,
        monkeypatch,
        successful_push=3,
    )

    assert result == 0
    assert calls_file.read_text(encoding="utf-8").splitlines() == [
        "pull",
        "push",
        "pull",
        "push",
        "pull",
        "push",
    ]
    assert (tmp_path / "public" / "index.html").read_text(encoding="utf-8") == "report"
    assert not (tmp_path / "pages-worktree" / ".gitlab-ci.yml").exists()

    commands = commands_file.read_text(encoding="utf-8").splitlines()
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
    result, calls_file, _ = run_publish_pages_worktree(
        tmp_path,
        monkeypatch,
        successful_push=1,
        remote_branch_exists=False,
    )

    assert result == 0
    assert calls_file.read_text(encoding="utf-8").splitlines() == ["push"]
    assert (tmp_path / "public" / "index.html").is_file()


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
