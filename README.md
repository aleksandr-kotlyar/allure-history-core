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

## Custom report groups

Set `--group-path` to place each new report in a custom relative directory,
instead of `{environment}/{branch}`. For example, add this to `build-report`:

```bash
--group-path "$QA_ENV/$CI_PROJECT_NAME/$NAMESPACE"
```

With `dev`, `qa_python`, and `qa-mdsp-latest`, Core creates:

```text
public/dev/qa_python/qa-mdsp-latest/
  job_123/
  job_124/
  latest/
  history/
```

The path can have one or many levels. Directories and navigation are created
automatically. CI expands the variables before calling Core; Core accepts the
resulting path literally, without evaluating variables or templates. Spaces and
Unicode names are preserved on disk and encoded in URLs. `--environment` and
`--branch` are optional when `--group-path` is set. Omitting it keeps the default
`{environment}/{branch}` layout.

Each exact group path has its own Allure history, `latest/`, and snapshot
retention. Include the branch or project in the path when they must have separate
histories. Snapshot IDs must be unique within a group; publishing the same ID
replaces that snapshot. Changing the path starts a new group; it does not move
old reports. Core's JSON output includes `group_path`, `snapshot_dir`, and the
actual `report_url`; executor links and adapter comments use the same path.

Paths must be relative, with no empty, `.` or `..` segments, backslashes, or
control characters. Report/service names are reserved as group segments:
`job_*`, timestamp-shaped `YYYY-MM-DD_HH-MM-SS`, `history`, `.history`, `latest`,
`widgets`, `data`, `index.html`, and `.modified_at`. A group cannot pass through
an existing report, file, or symlink.

The CI adapters accept `ALLURE_HISTORY_GROUP_PATH`. Their GitLab component,
GitHub action, and reusable workflow expose it as `group-path`. Use CI variables
in GitLab and expressions such as `${{ github.event.repository.name }}` in
GitHub; no separate tree schema is required. The same generated tree can be
uploaded to storage and indexed by the existing S3 navigation command.

Public smoke checks accept the same path:

```bash
allure-history smoke --base-url https://reports.example.test \
  --group-path dev/qa_python/qa-mdsp-latest --report job_124
```

## Existing report navigation

Build a separate HTML preview from a local tree of existing reports:

```bash
ALLURE_HISTORY_SHOW_INTRO=false ALLURE_HISTORY_SHOW_FOOTER=false \
allure-history index \
  --public-dir existing-reports \
  --output-dir navigation-preview \
  --report-base-url https://reports.example.test/prefix \
  --public-title "QA reports"
```

The output contains navigation pages and `latest/` redirects only. Directory
links stay relative to the preview; report links point to their original public
URLs. The source tree is never changed, no Allure CLI is invoked, and retention
does not run. The output must be empty and outside the source tree.

The explicit root can have any name. Groups can be nested to any depth, including
`{environment}/{project}/{namespace}/{YYYY-MM-DD_HH-MM-SS}/`. Both `history/` and
`.history/` are hidden. Reports are recognised by `job_NNN`, timestamp names,
`widgets/summary.json`, or an index with `widgets/` or `data/`. Indexing stops at
the report boundary even if summary JSON is missing or malformed. Symbolic links
are not followed. `build-report` keeps its default layout unless `--group-path`
is supplied; retention is scoped to the selected group.

Sorting uses `.modified_at`, the timestamp folder name, or summary start time;
numeric jobs retain their ID fallback. Filesystem download times do not decide
which report is latest. Reports without usable time metadata display `n/a`.
`ALLURE_HISTORY_SHOW_INTRO=false` removes the entire project-summary banner;
`ALLURE_HISTORY_SHOW_FOOTER=false` remains the existing footer switch. Both
default to visible in the legacy Pages flow. License notices remain in source.

## Publishing reports to S3

`publish-job` uses S3 as the persistent backend for the complete report cycle:
restore the tree/history, generate a new report through Core, apply per-group
retention, upload the report and history, refresh navigation, then prune old
objects. GitLab/GitHub metadata, optional MR/PR comments, and GitHub outputs
come from the same CI adapters as the git-backed publisher. No storage branch,
Git push token, or Pages deployment is needed.

```bash
ALLURE_HISTORY_GROUP_PATH="$QA_ENV/$CI_PROJECT_NAME/$NAMESPACE" \
REPORTS_TO_KEEP=30 \
allure-history-s3 publish-job --provider gitlab \
  --s3-uri s3://reports/team/project \
  --public-url https://reports.example.test/team/project
```

Use `--provider github` in GitHub Actions. The provider reads the same normal CI
variables and `allure-results`/`jobid` artifacts as its existing adapter. Optional
`ALLURE_HISTORY_RESULTS_DIR`, `ENV`, and `ALLURE_HISTORY_GROUP_PATH` retain their
existing meanings. `--public-title` defaults to `allure-history`; the intro banner
is hidden unless `ALLURE_HISTORY_SHOW_INTRO=true`. `--public-url` describes the
report origin, independently of the API endpoint or Pages. GitLab comments read
the actual report URL from Core output, so `CI_PAGES_URL` is not required here.

The default local tree is `allure-s3/public`; it must be empty before publication.
This implementation restores the full owned prefix, with bounded parallel
transfers, so large archives cost download time and disk space. Existing report
objects are not reuploaded; uploads cover the new snapshot, current group history,
navigation, and time metadata. Content types are set for new files. Old objects
are removed only after report and navigation uploads succeed. Retention must be
positive. Changing `group-path` creates a new group and preserves existing groups.

Publication requires a **dedicated non-root prefix**, exclusively owned by this
tree. Do not choose a prefix containing unrelated storage, or use overlapping
prefixes for independent publishers. All publishers must share the same prefix
and conditional lock, including the navigation commands below. A busy prefix
fails rather than overwriting a publisher's work. AWS credentials, endpoint and
addressing-style configuration use the standard boto3 chain.

S3 publication is atomic per object, not per report tree. A failed transfer can
leave partial new report files; old report retention and history are not advanced
when snapshot upload fails. A failed job can be rerun with the same snapshot ID
in a clean local workspace. Keep snapshot IDs unique across jobs/projects sharing
a group. Stop other publishers before inspecting a lock left by a killed job.
The navigation rollback command below restores navigation only, not report data
or retention deletions.

The GitLab `allure-history-s3` component and GitHub `s3` action expose this flow.
The separate `allure-history-s3-index` component and `s3-index` action manage
navigation for reports produced elsewhere.

## Existing reports in S3

The `allure-history-s3` adapter handles S3 I/O; the index generator stays local.
It uses boto3's normal AWS credential/configuration chain, including profiles,
workload identity and `AWS_ENDPOINT_URL_S3` for compatible endpoints.

Prepare a preview, an exact create/replace plan, and backups without writing S3:

```bash
ALLURE_HISTORY_SHOW_FOOTER=false allure-history-s3 index \
  --s3-uri s3://reports/team/project \
  --public-url https://reports.example.test/team/project \
  --public-title "Project reports" \
  --output-dir allure-navigation
```

`--public-url` must correspond to the selected S3 prefix; it need not resemble
the bucket address. Bucket-root indexing is supported without bucket-wide
synchronisation or deletion. Prefer a dedicated prefix when a smaller scope is
sufficient. All publishers of the same navigation tree must use the same prefix.
The S3 adapter hides the intro by default; the existing environment switch can
enable it explicitly.

Paths are existing object keys, not a template to expand. The adapter discovers
groups recursively and stops at each report; environment, project, namespace,
branch and report naming are not prescribed. Existing URLs remain unchanged.
For example, both `dev/main/job_123/` and
`dev/project/namespace/2026-10-26_09-00-00/` can live below the selected root.
Unknown report names need the usual Allure `index.html` and `widgets/` or `data/`
markers. Renaming reports or generating new reports into a custom layout is a
separate operation, outside this navigation command.

The credential chain and endpoint settings are the standard
[boto3 configuration](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).
Use `AWS_ENDPOINT_URL_S3` or a shared AWS config for an S3-compatible endpoint.
For an endpoint requiring path-style addressing, the shared config can contain:

```ini
[default]
s3 =
    addressing_style = path
```

The public URL is independently configured because a CDN, reverse proxy or
static website address can differ from the S3 API endpoint. The browser only
needs access to the public reports; AWS credentials stay in the publishing job.

Only the object listing, summaries, time metadata, and navigation objects being
replaced are downloaded. Original report HTML, attachments, executor files, and
history are never downloaded or uploaded. Missing summary data stays `n/a`.
The adapter keeps neighbouring projects, namespaces, and empty indexed groups.
For unnamed-time reports it falls back to the actual S3 index object's
LastModified time, never the local fetch time.

Review `allure-navigation/public/index.html` and `plan.json`. Preserve the whole
directory as the rollback artifact. These are separate, explicit write commands:

```bash
allure-history-s3 publish --plan allure-navigation/plan.json
allure-history-s3 rollback --plan allure-navigation/plan.json
```

Publication validates that the listed source and previous indexes have not
changed since preview, then uses conditional writes for only the planned HTML
objects. Each successful operation is recorded locally. A shared prefix lock
serialises publishers, including different CI jobs/projects using that scope.
Changed sources require a new preview; conflicts are never overwritten blindly.
New HTML uses `text/html; charset=utf-8` and `Cache-Control: no-cache`.

Before rollback, stop automatic GUI publication and download the complete
artifact from the publishing job, including partial failures. Rollback restores
previous HTML and saved HTTP metadata, and deletes only newly created objects
from that plan. It refuses to overwrite navigation changed by another publisher.
It can recover a partial upload or a lost upload response. Git revert alone does
not restore S3 objects. No `sync --delete` is used.

The endpoint must enforce conditional PUT/DELETE (`If-Match`/`If-None-Match`);
unsupported operations fail without an unconditional fallback. Publication is
atomic per object, not across the entire navigation tree. Raw report publishers
can add reports during publication; refresh navigation again after such runs.
Use bucket-policy access: per-object ACLs, tags, encryption overrides and object
lock settings are not preserved by the rollback artifact. Object modification
times/version IDs naturally change when HTML is restored. A killed process can
leave `.allure-history-index.lock`: inspect the plan and confirm no publisher is
running before manually removing that exact lock. No automatic expiry takeover
is attempted. Never include the rollback artifact under the indexed S3 prefix.

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
