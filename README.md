# Core Allure History

Provider-neutral runtime and CLI for static Allure report history.

`core-allure-history` owns report generation, history restoration, retention,
navigation indexes, executor metadata, and public Pages smoke checks. CI adapters
such as `gitlab-allure-history` and `github-allure-history` remain responsible for
downloading artifacts, preparing the persistent storage tree, pushing it, and
deploying Pages.

## CLI

Generate and update one report:

```bash
allure-history build-report \
  --results-dir allure-results \
  --public-dir pages-worktree/public \
  --environment dev \
  --branch feature-login \
  --report-id 123 \
  --pages-base-url https://example.github.io/project \
  --ci-name "GitHub Actions" \
  --ci-type github \
  --build-url https://github.com/example/project/actions/runs/123 \
  --build-name "GitHub Actions Run 123"
```

Create a stable branch slug:

```bash
allure-history slug "Feature/Login"
```

Smoke published Pages:

```bash
allure-history smoke \
  --base-url https://example.github.io/project \
  --environment dev \
  --branch feature-login \
  --report job_123
```

## Runtime Contract

The `build-report` command expects an existing or empty public storage tree. It:

1. Restores `{public}/{environment}/{branch}/history` into the Allure results.
2. Writes `executor.json`.
3. Runs `allure generate`.
4. Stores an immutable `job_{report-id}` snapshot.
5. Refreshes branch history and `.modified_at` metadata.
6. Prunes old snapshots and regenerates navigation indexes and `latest/`.

The command does not clone, commit, push, or deploy. Those operations remain in
the provider adapter.

## Development

```bash
python3 -m pip install -r requirements.txt
pytest
```

Tag pipelines build and publish the runtime image to the repository's GitLab
Container Registry. Adapter releases should pin the same
`YYYY.MINOR.PATCH` tag.
