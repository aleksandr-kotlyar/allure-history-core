import pytest

import github_adapter
import gitlab_adapter


@pytest.mark.parametrize("adapter", [github_adapter, gitlab_adapter])
@pytest.mark.parametrize(
    "passed,failed,broken,skipped,unknown,fragment",
    [
        (3, 0, 0, 0, 0, ""),
        (2, 1, 0, 0, 0, "#categories"),
        (2, 0, 1, 0, 0, "#categories"),
        (1, 1, 1, 0, 0, "#categories"),
        (2, 0, 0, 1, 0, ""),
        (0, 0, 0, 0, 1, ""),
        (0, 0, 0, 0, 0, ""),
    ],
)
def test_comment_link_opens_categories_only_for_failures(
    adapter, passed, failed, broken, skipped, unknown, fragment
):
    url = "https://reports.example.test/dev/feature/job_123/"
    statistic = {
        "passed": passed,
        "failed": failed,
        "broken": broken,
        "skipped": skipped,
        "unknown": unknown,
        "total": passed + failed + broken + skipped + unknown,
    }

    body = adapter.render_comment_body(url, statistic)

    assert f"]({url}{fragment})" in body
