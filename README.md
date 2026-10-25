# Allure History Core

Provider-neutral runtime and CLI for static Allure report history.

`allure-history-core` owns report generation, history restoration, retention,
navigation indexes, executor metadata, and public Pages smoke checks. CI adapters
such as `allure-history-gitlab` and `allure-history-github` remain responsible for
downloading artifacts, preparing the persistent storage tree, pushing it, and
deploying Pages.

## Projects

| Project | Responsibility |
| --- | --- |
| [allure-history-core](https://gitlab.com/aleksandr-kotlyar/allure-history-core) | Report generation, history, retention, and indexes. |
| [allure-history-gitlab](https://gitlab.com/aleksandr-kotlyar/allure-history-gitlab) | GitLab CI/CD, GitLab Pages, and merge request integration. |
| [allure-history-github](https://github.com/aleksandr-kotlyar/allure-history-github) | GitHub Actions, GitHub Pages, and pull request integration. |

Core development, issues, merge requests, and releases are maintained on GitLab.
The core's GitHub mirror is updated manually after changes are merged and validated.

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

GitHub CI builds the runtime, generates a real Allure smoke report, and publishes
the tested image as `ghcr.io/aleksandr-kotlyar/allure-history-core:<version>` for
release tags, or `sha-<commit>` for branch pushes. Publishing runs only in the
upstream repository. Pull requests build and smoke-test without publishing.

The image includes both `allure-history-github` and `allure-history-gitlab`
adapters. The GitHub adapter restores and updates the storage branch, runs Core,
and exports `report-url` and `public-dir` through `GITHUB_OUTPUT`. GitHub Pages
deployment remains in the calling workflow.

After the GitHub tag build succeeds, push the same tag to GitLab. Its tag
pipeline copies that image to
`registry.gitlab.com/aleksandr-kotlyar/allure-history-core:<version>` using the
existing GitLab CI registry credentials. It does not rebuild the runtime.
Keep the GHCR package public so consumers and forks can pull without credentials.
For branch validation, the manual GitLab mirror job copies the corresponding
`sha-<commit>` image after that commit's GitHub CI succeeds.

Adapters pin a tested core release independently of their own release version.
The `2026.4.1` image includes checksum-verified `s5cmd` for S3-compatible
storage and installs the runtime under `/opt/allure-history-core`.

## Manual GitHub Sync

Use a GitLab checkout with an explicit GitHub remote:

```bash
git clone --origin gitlab git@gitlab.com:aleksandr-kotlyar/allure-history-core.git
cd allure-history-core
git remote add github git@github.com:aleksandr-kotlyar/allure-history-core.git
git fetch gitlab --tags
git push github refs/remotes/gitlab/master:refs/heads/master
```

Publish release tags to GitHub first with `git push github <release-tag>`.
Wait for the image build and smoke check, then push the same tag to GitLab so
its registry mirror job can pull the published GHCR image.
