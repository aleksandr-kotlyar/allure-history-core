import re
from pathlib import Path
from urllib.parse import quote


def path_segment(value: str, option: str) -> str:
    if (not value or value in {".", ".."} or "/" in value or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"{option} must be one non-empty path segment")
    return value


def report_group_path(value: str, environment: str, branch: str) -> Path:
    parts = value.split("/") if value else [
        path_segment(environment, "--environment"), path_segment(branch, "--branch"),
    ]
    for part in parts:
        path_segment(part, "--group-path")
        if (part in {"history", ".history", "latest", "index.html", ".modified_at", "data", "widgets",
                     ".allure-history-navigation.json"}
                or part.startswith("job_")
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", part)):
            raise ValueError(f"--group-path contains a reserved report name: {part}")
    return Path(*parts)


def public_report_url(base_url: str, group_path: Path, report_name: str) -> str:
    return base_url.rstrip("/") + "/" + "/".join(
        quote(part, safe="") for part in (*group_path.parts, report_name)
    ) + "/"
