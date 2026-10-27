from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from allure_history import utc_timestamp
from report_paths import public_report_url, report_group_path


PAGES_WORKTREE = Path("pages-worktree")
PAGES_PUBLIC_DIR = PAGES_WORKTREE / "public"
PUBLIC_DIR = Path("public")
RESULTS_DIR = Path("allure-results")
OUTPUT_FILE = Path("allure-history-output.json")
MR_MARKER = "allure-history-report"


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_or(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def pages_branch() -> str:
    return env_or("PAGES_BRANCH", "gl-pages")


def report_env() -> str:
    return env_or("ENV", "dev")


def report_branch() -> str:
    return env_or("CI_COMMIT_REF_SLUG", "detached")


def job_id() -> str:
    jobid_file = Path("jobid")
    if jobid_file.is_file():
        value = jobid_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return require_env("CI_JOB_ID")


def report_dir_name() -> str:
    return f"job_{job_id()}"


def modified_at() -> str:
    return (
        os.environ.get("CI_PIPELINE_CREATED_AT", "").strip()
        or os.environ.get("CI_JOB_STARTED_AT", "").strip()
        or utc_timestamp()
    )


def image_tag() -> str:
    return (
        os.environ.get("ALLURE_HISTORY_IMAGE_TAG", "").strip()
        or os.environ.get("COMPONENT_VERSION", "").strip()
        or os.environ.get("COMPONENT_REFERENCE", "").strip()
    )


def component_version() -> str:
    return (
        os.environ.get("GITLAB_ALLURE_HISTORY_VERSION", "").strip()
        or os.environ.get("ALLURE_HISTORY_GENERATOR_VERSION", "").strip()
        or image_tag()
        or os.environ.get("COMPONENT_VERSION", "").strip()
        or os.environ.get("COMPONENT_REFERENCE", "").strip()
        or "unknown"
    )


def require_command(command: str, message: str) -> None:
    if shutil.which(command):
        return
    raise RuntimeError(message)


def validate_runtime_commands() -> None:
    require_command(
        "python3",
        "Missing python3 in the core runtime image. "
        "Use the default core image or provide python3 in your custom image.",
    )
    require_command(
        "allure-history",
        "Missing allure-history command in the core runtime image. "
        "Use the default core image or provide the wrapper entrypoint in your custom image.",
    )
    require_command(
        "allure",
        "Missing allure command in the core runtime image. "
        "Use the default core image or provide Allure commandline in your custom image.",
    )


def validate_image_tag_if_configured() -> None:
    if "ALLURE_HISTORY_IMAGE_TAG" not in os.environ:
        return

    if os.environ.get("ALLURE_HISTORY_IMAGE_TAG", "").strip():
        return

    raise RuntimeError(
        "ALLURE_HISTORY_IMAGE_TAG is empty. Provide a tagged component reference "
        "(for example @2026.x.y), or set the runtime image tag explicitly."
    )


def ensure_publish_runtime_available() -> None:
    ensure_git_available()
    validate_runtime_commands()
    validate_image_tag_if_configured()


def output_file_path() -> Path:
    return Path(env_or("ALLURE_HISTORY_OUTPUT_FILE", str(OUTPUT_FILE)))


def results_dir_path() -> Path:
    return Path(env_or("ALLURE_HISTORY_RESULTS_DIR", str(RESULTS_DIR)))


def public_dir_path() -> Path:
    return Path(env_or("ALLURE_HISTORY_PUBLIC_DIR", str(PAGES_PUBLIC_DIR)))


def dependencies_dir() -> Path:
    return Path(env_or("ALLURE_HISTORY_DEPENDENCIES_DIR", "."))


def copy_dependency_artifacts(job_names: tuple[str, ...]) -> None:
    base_dir = dependencies_dir()
    copied = False

    for job_name in job_names:
        source = base_dir / job_name / "allure-results"
        if not source.is_dir():
            continue
        destination = results_dir_path()
        destination.mkdir(parents=True, exist_ok=True)
        for entry in source.iterdir():
            target = destination / entry.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
        copied = True

    if copied:
        print("Copied combined allure-results from dependency artifacts.")


def ensure_results_present() -> None:
    results_dir = results_dir_path()
    if results_dir.is_dir() and any(results_dir.iterdir()):
        return

    copy_dependency_artifacts(("test_gate", "test_demo", "consumer_contract"))
    if results_dir.is_dir() and any(results_dir.iterdir()):
        return

    raise RuntimeError(
        "Missing Allure results. At least one earlier job must upload "
        "allure-results/ as an artifact."
    )


def report_url() -> str:
    try:
        data = json.loads(output_file_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("report_url"), str) and data["report_url"]:
        return data["report_url"]
    return public_report_url(
        require_env("CI_PAGES_URL"),
        report_group_path(os.environ.get("ALLURE_HISTORY_GROUP_PATH", ""), report_env(), report_branch()),
        report_dir_name(),
    )


def ensure_git_available() -> None:
    if shutil.which("git"):
        return
    raise RuntimeError(
        "Missing git in the core runtime image. "
        "Use the default core image or provide git in your custom image."
    )


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(["git", *args], cwd=cwd, check=check)


def prepare_pages_worktree() -> None:
    ensure_git_available()
    repository_url = require_env("CI_REPOSITORY_URL")
    branch = pages_branch()

    print(f"Preparing Pages worktree for branch '{branch}'.")
    result = git(
        "ls-remote",
        "--exit-code",
        "--heads",
        repository_url,
        f"refs/heads/{branch}",
        check=False,
    )

    if result.returncode == 0:
        git(
            "clone",
            "--single-branch",
            "--branch",
            branch,
            repository_url,
            str(PAGES_WORKTREE),
        )
        return

    if result.returncode == 2:
        print(f"Storage branch '{branch}' does not exist; creating it.")
        git("init", str(PAGES_WORKTREE))
        git("-C", str(PAGES_WORKTREE), "remote", "add", "origin", repository_url)
        git("-C", str(PAGES_WORKTREE), "checkout", "--orphan", branch)
        return

    raise RuntimeError(
        f"Failed to check storage branch '{branch}'. Verify repository access and CI network settings."
    )


def build_report(*, public_dir: Path | None = None, base_url: str = "", public_title: str = "",
                 skip_index: bool = False) -> None:
    validate_runtime_commands()
    jid = job_id()
    env_name = report_env()
    branch = report_branch()
    stamp = modified_at()
    ensure_results_present()

    group_path = os.environ.get("ALLURE_HISTORY_GROUP_PATH", "")
    group = report_group_path(group_path, env_name, branch)
    print(
        f"Building Allure report for group '{group.as_posix()}', job '{jid}'."
    )
    group_args = ["--group-path", group_path] if group_path else []
    run_command(
        [
            "allure-history",
            "build-report",
            "--results-dir",
            str(results_dir_path()),
            "--public-dir",
            str(public_dir if public_dir is not None else public_dir_path()),
            "--environment",
            env_name,
            "--branch",
            branch,
            "--report-id",
            jid,
            "--pages-base-url",
            base_url or require_env("CI_PAGES_URL"),
            "--ci-name",
            "GitLab CI",
            "--ci-type",
            "gitlab",
            "--build-url",
            require_env("CI_PIPELINE_URL"),
            "--build-name",
            f"GitLab Job Run {jid}",
            "--modified-at",
            stamp,
            "--reports-to-keep",
            env_or("REPORTS_TO_KEEP", "30"),
            "--public-title",
            public_title or "allure-history-gitlab",
            "--publisher-title",
            "Allure History GitLab",
            "--pages-name",
            "GitLab Pages",
            "--generator-name",
            "allure-history-gitlab",
            "--generator-url",
            "https://gitlab.com/aleksandr-kotlyar/allure-history-gitlab",
            "--generator-version",
            component_version(),
            "--generator-version-url",
            "https://gitlab.com/explore/catalog/aleksandr-kotlyar/allure-history-gitlab",
            "--output-file",
            str(output_file_path()),
        ]
        + group_args + (["--skip-index"] if skip_index else [])
    )


def copy_public_dir() -> None:
    if PUBLIC_DIR.exists():
        shutil.rmtree(PUBLIC_DIR)
    shutil.copytree(public_dir_path(), PUBLIC_DIR)


def publish_pages_worktree() -> None:
    require_env("GIT_PUSH_TOKEN")
    require_env("CI_SERVER_HOST")
    require_env("CI_PROJECT_PATH")

    branch = pages_branch()
    print(f"Publishing generated Pages tree to '{branch}'.")

    git("-C", str(PAGES_WORKTREE), "config", "user.name", "GitLab Runner")
    git(
        "-C",
        str(PAGES_WORKTREE),
        "config",
        "user.email",
        env_or("GITLAB_USER_EMAIL", "gitlab-ci@example.invalid"),
    )
    git(
        "-C",
        str(PAGES_WORKTREE),
        "remote",
        "set-url",
        "origin",
        "https://oauth2:{}@{}/{}.git".format(
            require_env("GIT_PUSH_TOKEN"),
            require_env("CI_SERVER_HOST"),
            require_env("CI_PROJECT_PATH"),
        ),
    )

    pages_ci = PAGES_WORKTREE / ".gitlab-ci.yml"
    if pages_ci.exists():
        pages_ci.unlink()

    git("-C", str(PAGES_WORKTREE), "add", "public")
    git("-C", str(PAGES_WORKTREE), "rm", "-f", "--ignore-unmatch", ".gitlab-ci.yml")

    diff_result = git(
        "-C",
        str(PAGES_WORKTREE),
        "diff",
        "--cached",
        "--quiet",
        check=False,
    )
    if diff_result.returncode == 0:
        print("No Pages changes to publish.")
        copy_public_dir()
        return

    git(
        "-C",
        str(PAGES_WORKTREE),
        "commit",
        "-m",
        f"Publish Allure report for pipeline {require_env('CI_PIPELINE_ID')} [skip ci]",
    )

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        remote_result = git(
            "-C",
            str(PAGES_WORKTREE),
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
            check=False,
        )

        if remote_result.returncode == 0:
            git("-C", str(PAGES_WORKTREE), "pull", "--rebase", "origin", branch)
        elif remote_result.returncode != 2:
            raise RuntimeError(f"Failed to check remote storage branch '{branch}'.")

        push_result = git(
            "-C",
            str(PAGES_WORKTREE),
            "push",
            "-o",
            "ci.skip",
            "origin",
            f"HEAD:{branch}",
            check=False,
        )
        if push_result.returncode == 0:
            copy_public_dir()
            return

        if attempt == max_attempts:
            raise RuntimeError(
                f"Failed to push Pages content after {max_attempts} attempts."
            )

        print(
            f"Pages push attempt {attempt}/{max_attempts} failed; retrying after rebase."
        )
        run_command(["sleep", "2"])


def load_summary_statistic(summary_path: Path) -> dict[str, int]:
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        statistic = data.get("statistic", {})
        passed = int(statistic.get("passed", 0))
        failed = int(statistic.get("failed", 0))
        broken = int(statistic.get("broken", 0))
        skipped = int(statistic.get("skipped", 0))
        unknown = int(statistic.get("unknown", 0))
        total = int(statistic.get("total", 0))
        if total == 0:
            total = passed + failed + broken + skipped + unknown
        return {
            "passed": passed,
            "failed": failed,
            "broken": broken,
            "skipped": skipped,
            "unknown": unknown,
            "total": total,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "passed": 0,
            "failed": 0,
            "broken": 0,
            "skipped": 0,
            "unknown": 0,
            "total": 0,
        }


def render_comment_body(url: str, statistic: dict[str, int]) -> str:
    failed = statistic["failed"]
    broken = statistic["broken"]
    total = statistic["total"]
    total_issues = failed + broken
    categories_url = f"{url}#categories"

    if total_issues > 0:
        return (
            "> [!caution]\n"
            "> \U0001f534 **FAILED** \u00b7 "
            f"[Allure report {total_issues} issues]({categories_url}) "
            f"(failed {failed}, broken {broken}) \u00b7 {total} total"
        )

    return (
        "> [!tip]\n"
        "> \U0001f7e2 **PASSED** \u00b7 "
        f"[Allure report]({categories_url}) \u00b7 {total} total"
    )


def gitlab_api_request(
    method: str,
    url: str,
    token: str,
    *,
    body: str | None = None,
) -> tuple[int, object | None]:
    headers = {
        "Accept": "application/json",
        "PRIVATE-TOKEN": token,
        "User-Agent": "allure-history-core-gitlab-adapter",
    }
    data = None
    if body is not None:
        payload = urlencode({"body": body}).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = payload

    request = Request(url, headers=headers, data=data, method=method)

    try:
        with urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            raw = response.read()
            if "application/json" in content_type and raw:
                return response.status, json.loads(raw)
            return response.status, raw.decode("utf-8") if raw else None
    except HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw.decode("utf-8") if raw else None
        return error.code, payload
    except URLError:
        return 0, None


def find_existing_note(notes: object, current_user_id: str) -> str:
    if not isinstance(notes, list):
        return "NONE"

    report_notes = [
        note
        for note in notes
        if isinstance(note, dict)
        and not note.get("system", False)
        and (
            "[Allure report" in str(note.get("body", ""))
            or MR_MARKER in str(note.get("body", ""))
        )
    ]
    own = [
        note
        for note in report_notes
        if str(note.get("author", {}).get("id", "")) == current_user_id
    ]
    foreign = [
        note
        for note in report_notes
        if str(note.get("author", {}).get("id", "")) != current_user_id
    ]

    if own:
        return f"OWN:{own[0]['id']}"
    if foreign:
        return f"FOREIGN:{len(foreign)}"
    return f"NONE:{len(report_notes)}/{len(notes)}"


def comment_mr() -> None:
    print(
        "MR comment: "
        f"ALLURE_HISTORY_COMMENT_MR={env_or('ALLURE_HISTORY_COMMENT_MR', 'unset')}, "
        f"CI_MERGE_REQUEST_IID={env_or('CI_MERGE_REQUEST_IID', 'unset')}"
    )

    if env_or("ALLURE_HISTORY_COMMENT_MR", "false") != "true":
        print("MR comment: skipping — ALLURE_HISTORY_COMMENT_MR is not 'true'.")
        return

    if not os.environ.get("CI_MERGE_REQUEST_IID", "").strip():
        print("MR comment: skipping — not in a merge request pipeline.")
        return

    token = os.environ.get("ALLURE_HISTORY_TOKEN", "").strip()
    if not token:
        print(
            'MR comment: skipping — ALLURE_HISTORY_TOKEN with "developer" role and "api" scope is not set.'
        )
        return

    statistic = load_summary_statistic(Path(report_dir_name()) / "widgets" / "summary.json")
    body = render_comment_body(report_url(), statistic)

    user_status, user_data = gitlab_api_request(
        "GET",
        f"{require_env('CI_API_V4_URL')}/user",
        token,
    )
    if user_status != 200 or not isinstance(user_data, dict):
        print("MR comment: failed to authenticate with ALLURE_HISTORY_TOKEN. Skipping MR comment.")
        return

    current_user_id = str(user_data.get("id", "")).strip()
    if not current_user_id:
        print("MR comment: failed to authenticate with ALLURE_HISTORY_TOKEN. Skipping MR comment.")
        return
    print(f"MR comment: authenticated as user id {current_user_id}")

    notes_url = (
        f"{require_env('CI_API_V4_URL')}/projects/{require_env('CI_PROJECT_ID')}"
        f"/merge_requests/{require_env('CI_MERGE_REQUEST_IID')}/notes?sort=desc&per_page=100"
    )
    _, notes_data = gitlab_api_request("GET", notes_url, token)
    match_info = find_existing_note(notes_data, current_user_id)
    note_action = "create"
    existing_note_id = ""

    if match_info.startswith("OWN:"):
        existing_note_id = match_info.removeprefix("OWN:")
        note_action = "update"
    elif match_info.startswith("FOREIGN:"):
        print(
            "MR comment: found "
            f"{match_info.removeprefix('FOREIGN:')} marker note(s) from other user(s); creating a new note for current token user."
        )
    elif match_info.startswith("NONE:"):
        counts = match_info.removeprefix("NONE:")
        marker_count, total_count = counts.split("/", 1)
        print(
            f"MR comment: no matching notes ({marker_count} marker, {total_count} total notes on MR)."
        )
    else:
        print("MR comment: no notes response from API.")

    note_base_url = (
        f"{require_env('CI_API_V4_URL')}/projects/{require_env('CI_PROJECT_ID')}"
        f"/merge_requests/{require_env('CI_MERGE_REQUEST_IID')}/notes"
    )
    if note_action == "update" and existing_note_id:
        print(f"MR comment: updating note {existing_note_id}...")
        status, _ = gitlab_api_request(
            "PUT",
            f"{note_base_url}/{existing_note_id}",
            token,
            body=body,
        )
        print(f"MR comment: HTTP {status:03d}")
        if status == 200:
            return
        if status == 403:
            print("MR comment: note cannot be updated (HTTP 403). Falling back to creating a new note.")
        elif status == 404:
            print("MR comment: note no longer exists (HTTP 404). Falling back to creating a new note.")
        else:
            print(f"MR comment: update failed (HTTP {status:03d}), falling back to creating a new note.")

    print("MR comment: creating new note...")
    status, _ = gitlab_api_request("POST", note_base_url, token, body=body)
    print(f"MR comment: HTTP {status:03d}")
    if status == 403:
        print(
            "MR comment: HTTP 403. ALLURE_HISTORY_TOKEN is authenticated but not allowed to create MR notes. "
            "Check api scope, project role, protected variables, and project access."
        )
    elif status == 401:
        print("MR comment: HTTP 401. ALLURE_HISTORY_TOKEN is not valid. Check the token value and its api scope.")
    elif status == 0:
        print("MR comment: HTTP 000. Network error reaching GitLab API. Check CI/CD network settings.")


def publish_job() -> None:
    print("Starting Allure History GitLab publish job.")
    ensure_publish_runtime_available()
    prepare_pages_worktree()
    build_report()
    publish_pages_worktree()
    comment_mr()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="GitLab adapter helpers for publishing Allure History Core reports."
    )
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("prepare-pages-worktree").set_defaults(
        handler=lambda _args: prepare_pages_worktree()
    )
    commands.add_parser("build-report").set_defaults(
        handler=lambda _args: build_report()
    )
    commands.add_parser("publish-pages-worktree").set_defaults(
        handler=lambda _args: publish_pages_worktree()
    )
    commands.add_parser("comment-mr").set_defaults(
        handler=lambda _args: comment_mr()
    )
    commands.add_parser("publish-job").set_defaults(
        handler=lambda _args: publish_job()
    )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        args.handler(args)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
