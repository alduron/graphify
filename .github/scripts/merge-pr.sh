#!/usr/bin/env bash
# Usage (all input via env): PR REPO HEAD_SHA GH_TOKEN
#   optional: AUTOMERGE_WORKFLOW_PAT   MERGE_ATTEMPTS=5   MERGE_SLEEP=15
#
# Squash-merges PR with --match-head-commit, retrying a bounded number of times: GitHub also
# rejects a merge mid-request when a sibling PR lands on the base first ("Base branch was
# modified"), which parked an approved green PR on a one-shot call. A retry stops at once when the
# live head has moved past HEAD_SHA (the newer run owns the merge).
#
# GITHUB_TOKEN stays the normal merge token. If a merge is refused with text containing
# "without `workflows` permission" (a PR that touches .github/workflows while behind base), the
# merge is retried ONCE with GH_TOKEN set to AUTOMERGE_WORKFLOW_PAT, same flags. One line says the
# fallback was used; the token value is never printed.
#
# Exit 0: merged, or the head moved (the newer run merges). Exit 1: every attempt failed.
set -uo pipefail
attempts="${MERGE_ATTEMPTS:-5}"
pause="${MERGE_SLEEP:-15}"
refusal='without `workflows` permission'
fallback_used=0

for i in $(seq 1 "$attempts"); do
  if out=$(gh pr merge "$PR" --repo "$REPO" --squash --delete-branch --match-head-commit "$HEAD_SHA" 2>&1); then
    printf '%s\n' "$out"
    exit 0
  fi
  printf '%s\n' "$out"
  if [ "$fallback_used" = "0" ] && [ -n "${AUTOMERGE_WORKFLOW_PAT:-}" ] \
     && printf '%s' "$out" | grep -qF "$refusal"; then
    fallback_used=1
    echo "merge refused for lack of workflows permission; retrying once with the AUTOMERGE_WORKFLOW_PAT fallback token."
    if out=$(GH_TOKEN="$AUTOMERGE_WORKFLOW_PAT" gh pr merge "$PR" --repo "$REPO" --squash --delete-branch --match-head-commit "$HEAD_SHA" 2>&1); then
      printf '%s\n' "$out"
      exit 0
    fi
    printf '%s\n' "$out"
  fi
  live_sha=$(gh pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid)
  if [ "$live_sha" != "$HEAD_SHA" ]; then
    echo "head moved $HEAD_SHA -> $live_sha; the newer run will merge."
    exit 0
  fi
  if [ "$i" -eq "$attempts" ]; then
    echo "merge attempt $i failed and head unchanged; giving up."
    exit 1
  fi
  echo "merge attempt $i failed (base branch likely modified by a sibling merge); retrying in ${pause}s"
  sleep "$pause"
done
