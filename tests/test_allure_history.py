import json
from pathlib import Path

import allure_history


def fake_allure(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-allure"
    executable.write_text(
        """#!/bin/sh
set -eu
results_dir="$2"
output_dir="$4"
mkdir -p "$output_dir/history" "$output_dir/widgets"
test -f "$results_dir/executor.json"
if [ -d "$results_dir/history" ]; then
  test -f "$results_dir/history/trend.json"
fi
printf '<html>report</html>\n' > "$output_dir/index.html"
printf '{}\n' > "$output_dir/history/history-trend.json"
printf '{"statistic":{"passed":1,"total":1},"time":{"duration":1}}\n' > "$output_dir/widgets/summary.json"
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_slug_normalizes_provider_branch_names():
    assert allure_history.slug("Feature/Login API") == "feature-login-api"
    assert allure_history.slug("///") == "detached"


def test_build_report_updates_snapshot_history_metadata_and_indexes(tmp_path):
    results_dir = tmp_path / "allure-results"
    results_dir.mkdir()
    (results_dir / "result.json").write_text("{}\n", encoding="utf-8")

    public_dir = tmp_path / "pages-worktree" / "public"
    previous_history = public_dir / "dev" / "feature-login" / "history"
    previous_history.mkdir(parents=True)
    (previous_history / "trend.json").write_text("{}\n", encoding="utf-8")

    output_file = tmp_path / "result.json"
    result = allure_history.main(
        [
            "build-report",
            "--results-dir",
            str(results_dir),
            "--public-dir",
            str(public_dir),
            "--work-dir",
            str(tmp_path),
            "--environment",
            "dev",
            "--branch",
            "feature-login",
            "--report-id",
            "123",
            "--pages-base-url",
            "https://example.github.io/project/",
            "--ci-name",
            "GitHub Actions",
            "--ci-type",
            "github",
            "--build-url",
            "https://github.com/example/project/actions/runs/123",
            "--build-name",
            "GitHub Actions Run 123",
            "--modified-at",
            "2026-06-14T09:00:00Z",
            "--reports-to-keep",
            "2",
            "--allure-command",
            str(fake_allure(tmp_path)),
            "--public-title",
            "allure-history-github",
            "--publisher-title",
            "Allure History GitHub",
            "--pages-name",
            "GitHub Pages",
            "--generator-name",
            "allure-history-github",
            "--generator-url",
            "https://github.com/example/allure-history-github",
            "--generator-version",
            "2026.1.0",
            "--generator-version-url",
            "https://github.com/example/allure-history-github/releases/tag/2026.1.0",
            "--output-file",
            str(output_file),
        ]
    )

    assert result == 0
    snapshot = public_dir / "dev" / "feature-login" / "job_123"
    assert (snapshot / "index.html").is_file()
    assert (snapshot / ".modified_at").read_text() == "2026-06-14T09:00:00Z\n"
    assert (public_dir / "dev" / "feature-login" / "history").is_dir()
    assert (public_dir / "dev" / "feature-login" / "latest" / "index.html").is_file()
    assert (public_dir / "index.html").is_file()

    executor = json.loads((results_dir / "executor.json").read_text())
    assert executor["type"] == "github"
    assert (
        executor["reportUrl"]
        == "https://example.github.io/project/dev/feature-login/job_123/"
    )

    output = json.loads(output_file.read_text())
    assert output["report_name"] == "job_123"
    assert output["snapshot_dir"] == str(snapshot)
    assert (
        'href="https://github.com/example/allure-history-github/releases/tag/2026.1.0"'
        in (public_dir / "index.html").read_text()
    )


def test_build_report_rejects_path_traversal(tmp_path):
    results_dir = tmp_path / "allure-results"
    results_dir.mkdir()
    (results_dir / "result.json").write_text("{}\n", encoding="utf-8")

    result = allure_history.main(
        [
            "build-report",
            "--results-dir",
            str(results_dir),
            "--public-dir",
            str(tmp_path / "public"),
            "--environment",
            "../dev",
            "--branch",
            "main",
            "--report-id",
            "1",
            "--pages-base-url",
            "https://example.test",
            "--ci-name",
            "CI",
            "--ci-type",
            "ci",
            "--build-url",
            "https://example.test/build/1",
            "--build-name",
            "Build 1",
        ]
    )

    assert result == 1


def test_write_executor_encodes_report_path_segments(tmp_path):
    report_url = allure_history.write_executor(
        tmp_path,
        pages_base_url="https://example.test/project/",
        group_path=Path("test env/feature login"),
        report_name="job_123",
        ci_name="CI",
        ci_type="ci",
        build_url="https://example.test/build/123",
        build_name="Build 123",
        build_order="123",
    )

    assert (
        report_url
        == "https://example.test/project/test%20env/feature%20login/job_123/"
    )
