#!/usr/bin/env bash
set -euo pipefail

image="${1:?Usage: smoke-runtime.sh IMAGE}"
smoke_dir="$(mktemp -d)"
trap 'rm -rf "$smoke_dir"' EXIT
mkdir -p "$smoke_dir/allure-results"
cp tests/fixtures/allure-results/smoke-result.json "$smoke_dir/allure-results/"

docker run --rm --entrypoint sh "$image" -ec '
  allure-history --help >/dev/null
  allure-history-github --help >/dev/null
  allure-history-gitlab --help >/dev/null
  s5cmd version
'

docker run --rm --user "$(id -u):$(id -g)" \
  -v "$smoke_dir:/work" -w /work \
  -e GITHUB_REPOSITORY=example/fork \
  -e GITHUB_REF_NAME=smoke \
  -e GITHUB_RUN_ID=1 \
  --entrypoint allure-history-github "$image" build-report

report="$smoke_dir/pages-worktree/public/dev/smoke/job_1"
test -s "$report/index.html"
python3 - "$report/widgets/summary.json" <<'PY'
import json
import sys
from pathlib import Path

statistic = json.loads(Path(sys.argv[1]).read_text())["statistic"]
assert statistic["total"] == 1 and statistic["passed"] == 1, statistic
PY
