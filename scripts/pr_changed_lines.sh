#!/usr/bin/env bash
# Write the changed-lines manifest for a pull request checkout.
#
#   pr_changed_lines.sh BASE_SHA HEAD_SHA PROJECT OUTPUT [--include-deletions]
#
# Line numbers must describe the tree the analyzer actually scans, so the diff
# ends at the checked-out commit, not at the PR head SHA:
#   * GitHub's merge commit (parents = [base side, HEAD_SHA], the
#     actions/checkout default on pull_request): diff against its first parent,
#     which needs only that one commit (fetched at depth 1 when missing).
#   * Anything else (ref: head.sha, a rebased checkout): diff against
#     merge-base(BASE_SHA, HEAD), which needs history; without it this fails
#     instead of producing a manifest for the wrong lines.
# Paths are relative to PROJECT (--relative), matching what the analyzer
# reports for `code-quality-analyzer PROJECT`. The PR controls the checkout, so
# external diff drivers, textconv and in-tree .gitattributes (which could mark
# every file binary and hide it) are all disabled.
set -euo pipefail

if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
  echo "usage: $0 BASE_SHA HEAD_SHA PROJECT OUTPUT [--include-deletions]" >&2
  exit 64
fi
base_sha=$1 head_sha=$2 project=$3 output=$4
convert_args=(--output "$output")
case "${5:-}" in
  "") ;;
  --include-deletions) convert_args+=(--include-deletions) ;;
  *) echo "::error::changed-lines: unknown option ${5}" ; exit 64 ;;
esac

sha_re='^([0-9a-f]{40}|[0-9a-f]{64})$'
if ! [[ $base_sha =~ $sha_re && $head_sha =~ $sha_re ]]; then
  echo "::error::changed-lines: pull_request base/head SHA is not a full hex object id"
  exit 1
fi

g() { git -C "$project" -c core.quotePath=true "$@"; }
have() { g cat-file -e "$1^{commit}" 2>/dev/null; }
need() {
  have "$1" || g fetch --quiet --no-tags --depth=1 origin "$1" || true
  if ! have "$1"; then
    echo "::error::changed-lines: commit $1 is not available locally and could not be fetched; use actions/checkout with fetch-depth: 0, or set changed-lines: false"
    exit 1
  fi
}

checkout=$(g rev-parse --verify "HEAD^{commit}")
parents=()
while IFS= read -r parent; do parents+=("$parent"); done \
  < <(g cat-file commit "$checkout" | sed -n '/^$/q;s/^parent //p')

if [ "${#parents[@]}" -eq 2 ] && [ "${parents[1]}" = "$head_sha" ]; then
  from=${parents[0]}
  need "$from"
  echo "changed-lines: merge commit $checkout against its base parent $from"
else
  need "$base_sha"
  if ! from=$(g merge-base "$base_sha" "$checkout" 2>/dev/null); then
    echo "::error::changed-lines: no merge base between $base_sha and the checkout $checkout (shallow history?); use actions/checkout with fetch-depth: 0, or set changed-lines: false"
    exit 1
  fi
  echo "changed-lines: checkout $checkout against merge-base $from"
fi

empty_tree=$(g hash-object -t tree /dev/null)
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
g --attr-source="$empty_tree" diff \
  --no-ext-diff --no-textconv --no-color --relative --find-renames \
  --src-prefix=a/ --dst-prefix=b/ --unified=0 --diff-filter=AMR \
  "$from" "$checkout" -- . \
  | "${CQA_PYTHON:-python}" "$script_dir/changed_lines.py" "${convert_args[@]}"
