from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from allure_history import slug, utc_timestamp
from report_paths import public_report_url, report_group_path


PAGES_WORKTREE = Path("pages-worktree")
PAGES_PUBLIC_DIR = PAGES_WORKTREE / "public"
PUBLIC_DIR = Path("public")
RESULTS_DIR = Path("allure-results")
JOB_ID_FILE = Path(".artifacts/jobid/jobid")
OUTPUT_FILE = Path("allure-history-output.json")
PR_MARKER = "<!-- github-allure-history-report -->"


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_or(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def pages_branch() -> str:
    return env_or("PAGES_BRANCH", "gh-pages")


def report_env() -> str:
    return env_or("ENV", "dev")


def repository_parts() -> tuple[str, str]:
    repository = require_env("GITHUB_REPOSITORY")
    if "/" not in repository:
        raise RuntimeError("GITHUB_REPOSITORY must have owner/name format")
    owner, name = repository.split("/", 1)
    if not owner or not name:
        raise RuntimeError("GITHUB_REPOSITORY must have owner/name format")
    return owner, name


def report_branch() -> str:
    return slug(env_or("GITHUB_HEAD_REF", env_or("GITHUB_REF_NAME", "detached")))


def job_id() -> str:
    configured = os.environ.get("ALLURE_HISTORY_JOB_ID", "").strip()
    if configured:
        return configured
    if JOB_ID_FILE.is_file():
        value = JOB_ID_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    return require_env("GITHUB_RUN_ID")


def report_dir_name() -> str:
    return f"job_{job_id()}"


def output_file_path() -> Path:
    return Path(env_or("ALLURE_HISTORY_OUTPUT_FILE", str(OUTPUT_FILE)))


def results_dir_path() -> Path:
    return Path(env_or("ALLURE_HISTORY_RESULTS_DIR", str(RESULTS_DIR)))


def public_dir_path() -> Path:
    return Path(env_or("ALLURE_HISTORY_PUBLIC_DIR", str(PAGES_PUBLIC_DIR)))


def server_url() -> str:
    return env_or("GITHUB_SERVER_URL", "https://github.com").rstrip("/")


def repository_remote_url() -> str:
    parsed = urlparse(server_url())
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError("GITHUB_SERVER_URL must be an absolute URL")
    return "{}://x-access-token:{}@{}/{}.git".format(
        parsed.scheme,
        require_env("GITHUB_TOKEN"),
        parsed.netloc,
        require_env("GITHUB_REPOSITORY"),
    )


def default_pages_base_url() -> str:
    owner, repository = repository_parts()
    if repository.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner}.github.io"
    return f"https://{owner}.github.io/{repository}"


def pages_base_url() -> str:
    value = os.environ.get("PAGES_BASE_URL_INPUT", "").strip()
    return value.rstrip("/") if value else default_pages_base_url()


def build_url() -> str:
    return "{}/{}/actions/runs/{}".format(
        server_url(),
        require_env("GITHUB_REPOSITORY"),
        require_env("GITHUB_RUN_ID"),
    )


def generator_version() -> str:
    configured = (
        os.environ.get("GITHUB_ALLURE_HISTORY_VERSION", "").strip()
        or os.environ.get("ALLURE_HISTORY_GENERATOR_VERSION", "").strip()
    )
    if configured:
        return configured

    action_ref = os.environ.get("GITHUB_ACTION_REF", "").strip()
    if action_ref:
        return action_ref

    workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "").strip()
    if "@" in workflow_ref:
        return workflow_ref.rsplit("@", 1)[1]

    return env_or("GITHUB_REF_NAME", "unknown")


def generator_version_url() -> str:
    version = generator_version()
    if version and version != "unknown":
        return (
            "https://github.com/aleksandr-kotlyar/allure-history-github/releases/tag/"
            f"{quote(version, safe='')}"
        )
    return ""


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
        "git",
        "Missing git in the core runtime image. "
        "Use the default core image or provide git in your custom image.",
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


def ensure_results_present() -> None:
    results_dir = results_dir_path()
    if results_dir.is_dir() and any(results_dir.iterdir()):
        return

    raise RuntimeError(
        "Missing Allure results. A previous job must upload allure-results/ as an artifact."
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


def git(
    *args: str,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_command(["git", *args], cwd=cwd, check=check)


def prepare_pages_worktree() -> None:
    remote_url = repository_remote_url()
    branch = pages_branch()

    print(f"Preparing GitHub Pages worktree for branch '{branch}'.")
    result = git(
        "ls-remote",
        "--exit-code",
        "--heads",
        remote_url,
        f"refs/heads/{branch}",
        check=False,
    )

    if result.returncode == 0:
        git(
            "clone",
            "--single-branch",
            "--branch",
            branch,
            remote_url,
            str(PAGES_WORKTREE),
        )
        return

    if result.returncode == 2:
        print(f"Storage branch '{branch}' does not exist; creating it.")
        git("init", str(PAGES_WORKTREE))
        git("-C", str(PAGES_WORKTREE), "remote", "add", "origin", remote_url)
        git("-C", str(PAGES_WORKTREE), "checkout", "--orphan", branch)
        return

    raise RuntimeError(
        f"Failed to check storage branch '{branch}'. Verify repository access."
    )


def build_report(*, public_dir: Path | None = None, base_url: str = "", public_title: str = "",
                 skip_index: bool = False) -> None:
    validate_runtime_commands()
    ensure_results_present()

    jid = job_id()
    env_name = report_env()
    branch = report_branch()
    stamp = utc_timestamp()

    group_path = os.environ.get("ALLURE_HISTORY_GROUP_PATH", "")
    group = report_group_path(group_path, env_name, branch)
    print(
        f"Building Allure report for group '{group.as_posix()}', run '{jid}'."
    )
    command = [
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
        base_url or pages_base_url(),
        "--ci-name",
        "GitHub Actions",
        "--ci-type",
        "github",
        "--build-url",
        build_url(),
        "--build-name",
        f"GitHub Actions Run {jid}",
        "--modified-at",
        stamp,
        "--reports-to-keep",
        env_or("REPORTS_TO_KEEP", "30"),
        "--public-title",
        public_title or "allure-history-github",
        "--publisher-title",
        "Allure History GitHub",
        "--pages-name",
        "GitHub Pages",
        "--generator-name",
        "allure-history-github",
        "--generator-url",
        "https://github.com/aleksandr-kotlyar/allure-history-github",
        "--generator-version",
        generator_version(),
        "--output-file",
        str(output_file_path()),
    ]
    version_url = generator_version_url()
    if version_url:
        command.extend(["--generator-version-url", version_url])
    if group_path:
        command.extend(["--group-path", group_path])
    if skip_index:
        command.append("--skip-index")

    run_command(command)


def copy_public_dir() -> None:
    source = public_dir_path()
    if not source.is_dir():
        raise RuntimeError(f"Generated public directory does not exist: {source}")
    if PUBLIC_DIR.exists():
        shutil.rmtree(PUBLIC_DIR)
    shutil.copytree(source, PUBLIC_DIR)


def publish_pages_worktree() -> None:
    branch = pages_branch()
    print(f"Publishing generated Pages tree to '{branch}'.")

    git("-C", str(PAGES_WORKTREE), "config", "user.name", "github-actions[bot]")
    git(
        "-C",
        str(PAGES_WORKTREE),
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )

    git("-C", str(PAGES_WORKTREE), "add", "public")

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
        f"Publish Allure report for run {require_env('GITHUB_RUN_ID')} [skip ci]",
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


def output_data() -> dict[str, object]:
    try:
        data = json.loads(output_file_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def report_url() -> str:
    data = output_data()
    value = data.get("report_url")
    if isinstance(value, str) and value:
        return value

    return public_report_url(
        pages_base_url(),
        report_group_path(os.environ.get("ALLURE_HISTORY_GROUP_PATH", ""), report_env(), report_branch()),
        report_dir_name(),
    )


def summary_path() -> Path:
    data = output_data()
    value = data.get("snapshot_dir")
    if isinstance(value, str) and value:
        return Path(value) / "widgets" / "summary.json"
    group = report_group_path(os.environ.get("ALLURE_HISTORY_GROUP_PATH", ""), report_env(), report_branch())
    return public_dir_path() / group / report_dir_name() / "widgets" / "summary.json"


def load_summary_statistic(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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
            f"{PR_MARKER}\n"
            "> [!CAUTION]\n"
            "> **FAILED** - "
            f"[Allure report {total_issues} issues]({categories_url}) "
            f"(failed {failed}, broken {broken}) - {total} total"
        )

    return (
        f"{PR_MARKER}\n"
        "> [!TIP]\n"
        f"> **PASSED** - [Allure report]({categories_url}) - {total} total"
    )


def github_api_request(
    method: str,
    url: str,
    token: str,
    *,
    body: dict[str, str] | None = None,
) -> tuple[int, object | None]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "allure-history-github",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    request = Request(url, headers=headers, data=data, method=method)

    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            if raw:
                return response.status, json.loads(raw)
            return response.status, None
    except HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw.decode("utf-8") if raw else None
        return error.code, payload
    except URLError:
        return 0, None


def find_existing_comment(comments: object) -> str:
    if not isinstance(comments, list):
        return ""
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        if PR_MARKER in str(comment.get("body", "")):
            return str(comment.get("id", ""))
    return ""


def comment_pr() -> None:
    print(
        "PR comment: "
        f"ALLURE_HISTORY_COMMENT_PR={env_or('ALLURE_HISTORY_COMMENT_PR', 'unset')}, "
        f"GITHUB_PR_NUMBER={env_or('GITHUB_PR_NUMBER', 'unset')}"
    )

    if env_or("ALLURE_HISTORY_COMMENT_PR", "false") != "true":
        print("PR comment: skipping because ALLURE_HISTORY_COMMENT_PR is not 'true'.")
        return

    issue_number = os.environ.get("GITHUB_PR_NUMBER", "").strip()
    if not issue_number:
        print("PR comment: skipping because no pull request number was provided.")
        return

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("PR comment: skipping because GITHUB_TOKEN is not set.")
        return

    statistic = load_summary_statistic(summary_path())
    body = render_comment_body(report_url(), statistic)
    api_url = env_or("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repository = require_env("GITHUB_REPOSITORY")
    comments_url = f"{api_url}/repos/{repository}/issues/{issue_number}/comments"

    page = 1
    while True:
        query = "?per_page=100" + (f"&page={page}" if page > 1 else "")
        _, comments = github_api_request("GET", comments_url + query, token)
        existing_comment_id = find_existing_comment(comments)
        if existing_comment_id or not isinstance(comments, list) or len(comments) < 100:
            break
        page += 1

    if existing_comment_id:
        print(f"PR comment: updating comment {existing_comment_id}...")
        status, _ = github_api_request(
            "PATCH",
            f"{api_url}/repos/{repository}/issues/comments/{existing_comment_id}",
            token,
            body={"body": body},
        )
        print(f"PR comment: HTTP {status:03d}")
        if status == 200:
            return
        print("PR comment: update failed; falling back to creating a new comment.")

    print("PR comment: creating new comment...")
    status, _ = github_api_request("POST", comments_url, token, body={"body": body})
    print(f"PR comment: HTTP {status:03d}")
    if status != 201:
        raise RuntimeError(f"Failed to create GitHub PR comment (HTTP {status:03d})")


def write_outputs(public_dir: Path = PUBLIC_DIR) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        url = report_url()
        if "\n" in url or "\r" in url:
            raise RuntimeError("Report URL must be a single line")
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"report-url={url}\npublic-dir={public_dir}\n")


def publish_job() -> None:
    print("Starting GitHub Allure History publish job.")
    validate_runtime_commands()
    ensure_results_present()
    prepare_pages_worktree()
    build_report()
    publish_pages_worktree()
    write_outputs()
    comment_pr()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="GitHub adapter helpers for publishing Core Allure History reports."
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
    commands.add_parser("comment-pr").set_defaults(
        handler=lambda _args: comment_pr()
    )
    commands.add_parser("publish-job").set_defaults(
        handler=lambda _args: publish_job()
    )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        args.handler(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
