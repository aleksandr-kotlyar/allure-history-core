from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from prune_reports import DEFAULT_REPORTS_TO_KEEP, prune_reports
from report_paths import path_segment, public_report_url, report_group_path
from smoke_public_pages import (
    DEFAULT_ATTEMPTS,
    DEFAULT_DELAY_SECONDS,
    public_page_urls,
    wait_for_public_pages,
)


def slug(value: str) -> str:
    result = []
    previous_dash = False

    for character in value.strip().lower():
        if character.isascii() and character.isalnum():
            result.append(character)
            previous_dash = False
        elif result and not previous_dash:
            result.append("-")
            previous_dash = True

    return "".join(result).strip("-") or "detached"


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_executor(
    results_dir: Path,
    *,
    pages_base_url: str,
    group_path: Path,
    report_name: str,
    ci_name: str,
    ci_type: str,
    build_url: str,
    build_name: str,
    build_order: str,
) -> str:
    report_url = public_report_url(pages_base_url, group_path, report_name)
    executor = {
        "name": ci_name,
        "type": ci_type,
        "reportName": "Allure Report with history",
        "reportUrl": report_url,
        "buildUrl": build_url,
        "buildName": build_name,
        "buildOrder": build_order,
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "executor.json").write_text(
        json.dumps(executor, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_url


def update_modified_at(path: Path, public_dir: Path, modified_at: str) -> None:
    current = path

    while True:
        metadata_file = current / ".modified_at"
        previous = (
            metadata_file.read_text(encoding="utf-8").strip()
            if metadata_file.is_file()
            else ""
        )
        if previous < modified_at:
            metadata_file.write_text(f"{modified_at}\n", encoding="utf-8")

        if current == public_dir:
            return
        current = current.parent


def configure_branding(args: argparse.Namespace) -> None:
    values = {
        "ALLURE_HISTORY_PUBLIC_TITLE": args.public_title,
        "ALLURE_HISTORY_PUBLISHER_TITLE": args.publisher_title,
        "ALLURE_HISTORY_PAGES_NAME": args.pages_name,
        "ALLURE_HISTORY_GENERATOR_NAME": args.generator_name,
        "ALLURE_HISTORY_GENERATOR_URL": args.generator_url,
        "ALLURE_HISTORY_VERSION": args.generator_version,
        "ALLURE_HISTORY_VERSION_URL": args.generator_version_url,
    }
    os.environ.update({key: value for key, value in values.items() if value is not None})


def build_report(args: argparse.Namespace) -> int:
    configure_branding(args)
    from generate_index import is_indexable_folder

    group_path = report_group_path(args.group_path, args.environment, args.branch)
    report_id = path_segment(args.report_id, "--report-id")

    results_dir = Path(args.results_dir).resolve()
    public_dir = Path(args.public_dir).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_name = f"job_{report_id}"
    generated_report_dir = work_dir / report_name
    group_dir = public_dir / group_path
    history_dir = group_dir / "history"

    current = public_dir
    for part in group_path.parts:
        current /= part
        if current.is_symlink() or (current.exists() and not is_indexable_folder(current)):
            raise ValueError(f"Report group must use directories outside existing reports: {current}")

    if not results_dir.is_dir() or not any(results_dir.iterdir()):
        raise RuntimeError(
            "Missing Allure results. The results directory must contain test output."
        )

    group_dir.mkdir(parents=True, exist_ok=True)
    results_history = results_dir / "history"
    if results_history.exists():
        shutil.rmtree(results_history)

    if history_dir.is_dir():
        shutil.copytree(history_dir, results_history)
        print(f"Restored previous Allure history from {history_dir}.")
    else:
        print(f"No previous Allure history for '{group_path.as_posix()}'.")

    report_url = write_executor(
        results_dir,
        pages_base_url=args.pages_base_url,
        group_path=group_path,
        report_name=report_name,
        ci_name=args.ci_name,
        ci_type=args.ci_type,
        build_url=args.build_url,
        build_name=args.build_name,
        build_order=report_id,
    )

    subprocess.run(
        [
            args.allure_command,
            "generate",
            str(results_dir),
            "-o",
            str(generated_report_dir),
            "--clean",
        ],
        check=True,
    )

    snapshot_dir = group_dir / report_name
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    shutil.copytree(generated_report_dir, snapshot_dir)

    modified_at = args.modified_at or utc_timestamp()
    (snapshot_dir / ".modified_at").write_text(
        f"{modified_at}\n",
        encoding="utf-8",
    )

    if history_dir.exists():
        shutil.rmtree(history_dir)
    generated_history = generated_report_dir / "history"
    if generated_history.is_dir():
        shutil.copytree(generated_history, history_dir)
    else:
        history_dir.mkdir(parents=True)

    update_modified_at(group_dir, public_dir, modified_at)
    prune_reports(group_dir, args.reports_to_keep)

    from generate_index import index_tree

    index_tree(public_dir)

    result = {
        "report_id": report_id,
        "report_name": report_name,
        "group_path": group_path.as_posix(),
        "report_url": report_url,
        "snapshot_dir": str(snapshot_dir),
    }
    if args.output_file:
        Path(args.output_file).write_text(
            json.dumps(result, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result))
    return 0


def smoke(args: argparse.Namespace) -> int:
    if args.attempts < 1 or args.delay_seconds < 0:
        raise ValueError("--attempts must be positive and --delay-seconds non-negative")

    urls = public_page_urls(
        args.base_url,
        args.environment,
        args.branch,
        args.report,
        group_path=args.group_path,
    )
    wait_for_public_pages(
        urls,
        attempts=args.attempts,
        delay_seconds=args.delay_seconds,
    )
    return 0


def http_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Report base URL must be HTTP(S), without credentials, query or fragment")
    return value.rstrip("/")


def index_reports(args: argparse.Namespace) -> int:
    import generate_index

    source = Path(args.public_dir).resolve()
    output = Path(args.output_dir).resolve()
    base_url = http_base_url(args.report_base_url)
    if not source.is_dir() or generate_index.is_report_folder(source):
        raise ValueError("--public-dir must be a navigation root, not an Allure report")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("--output-dir must be separate from the source tree")
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output-dir must be empty")

    previous_title = generate_index.PUBLIC_TITLE
    try:
        generate_index.PUBLIC_TITLE = args.public_title
        generate_index.index_tree(source, output_dir=output, report_base_url=base_url)
    finally:
        generate_index.PUBLIC_TITLE = previous_title
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Build and verify static Allure report history."
    )
    commands = root.add_subparsers(dest="command", required=True)

    slug_parser = commands.add_parser("slug", help="Create a URL-safe branch slug.")
    slug_parser.add_argument("value")
    slug_parser.set_defaults(handler=lambda args: print(slug(args.value)) or 0)

    build = commands.add_parser(
        "build-report",
        help="Generate one report snapshot and update the static history tree.",
    )
    build.add_argument("--results-dir", default="allure-results")
    build.add_argument("--public-dir", required=True)
    build.add_argument("--work-dir", default=".")
    build.add_argument("--environment", default="")
    build.add_argument("--branch", default="")
    build.add_argument("--group-path", default="", help="Relative report group; defaults to environment/branch.")
    build.add_argument("--report-id", required=True)
    build.add_argument("--pages-base-url", required=True)
    build.add_argument("--ci-name", required=True)
    build.add_argument("--ci-type", required=True)
    build.add_argument("--build-url", required=True)
    build.add_argument("--build-name", required=True)
    build.add_argument("--modified-at", default="")
    build.add_argument("--reports-to-keep", type=int, default=DEFAULT_REPORTS_TO_KEEP)
    build.add_argument("--allure-command", default="allure")
    build.add_argument("--public-title", default="allure-history")
    build.add_argument("--publisher-title", default="Allure History Publisher")
    build.add_argument("--pages-name", default="static Pages")
    build.add_argument("--generator-name", default="allure-history-core")
    build.add_argument(
        "--generator-url",
        default="https://gitlab.com/aleksandr-kotlyar/allure-history-core",
    )
    build.add_argument("--generator-version", default="")
    build.add_argument("--generator-version-url", default="")
    build.add_argument("--output-file")
    build.set_defaults(handler=build_report)

    index = commands.add_parser("index", help="Preview navigation for existing reports; never modifies the source.")
    index.add_argument("--public-dir", required=True)
    index.add_argument("--output-dir", required=True)
    index.add_argument("--report-base-url", required=True)
    index.add_argument("--public-title", default=os.environ.get("ALLURE_HISTORY_PUBLIC_TITLE", "allure-history"))
    index.set_defaults(handler=index_reports)

    smoke_parser = commands.add_parser(
        "smoke",
        help="Wait until public report navigation and snapshot URLs are available.",
    )
    smoke_parser.add_argument("--base-url", required=True)
    smoke_parser.add_argument("--environment", default="")
    smoke_parser.add_argument("--branch", default="")
    smoke_parser.add_argument("--group-path", default="")
    smoke_parser.add_argument("--report", required=True)
    smoke_parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    smoke_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
    )
    smoke_parser.set_defaults(handler=smoke)

    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return args.handler(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"allure-history: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
