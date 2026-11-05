#!/usr/bin/env bash
# Usage (all input via env): PR REPO HEAD_SHA   [STAMP_WAIT_TOTAL=120 STAMP_WAIT_INTERVAL=15]
#
# Called by automerge ONLY after it has confirmed the PR carries review:approved and that no
# approval stamp (from the github-actions bot) exists for the head. That is a skip, but not
# an innocent one when the approval is otherwise complete: if a valid verdict-approve token exists
# for the live head with no reject token, the PR is approved, green and stuck, and exiting 0 would
# report a healthy check on a PR nothing will ever merge
# (Caveat_ApprovalLabelOnADraftNeverGetsStampedSoAutomergeSkipsSilently).
#
# But an ordinary approval can look the same for a moment: the label lands while the default run
# is still in flight, and the separate labeled run posts the stamp tens of seconds later
# (Caveat_AutomergeStampArrivesFromTheLabeledRunSoTheOriginalRunMustWaitForIt). So this waits a
# bounded STAMP_WAIT_TOTAL seconds (re-checking every STAMP_WAIT_INTERVAL) before declaring the
# stranded state, mirroring the api repo's automerge wait.
#
# Exit 0: not the inconsistent state: no valid token, a reject token, the live head differs from
#   the event head (a newer run owns it), the PR is merged/closed, the label is gone, or the stamp
#   appeared during the wait. Silent: no comment.
# Exit 1: after the full wait, label + valid token + no stamp for the live head. Posts ONE marker
#   comment per head sha saying what is missing and how to unstick it, then fails so the check is red.
set -euo pipefail

total="${STAMP_WAIT_TOTAL:-120}"
interval="${STAMP_WAIT_INTERVAL:-15}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Fetch the live PR state into $tmp/pr.json. Sets nothing else; callers read it with jq.
fetch() {
  gh pr view "$PR" --repo "$REPO" --json state,labels,headRefOid,comments > "$tmp/pr.json"
}

# Return 0 when this run should stand down silently (merged/closed, head moved, label gone).
stand_down() {
  local state live
  state="$(jq -r '.state' "$tmp/pr.json")"
  live="$(jq -r '.headRefOid' "$tmp/pr.json")"
  if [ "$state" != "OPEN" ]; then
    echo "PR #$PR is $state; nothing to flag."; return 0
  fi
  if [ "$live" != "$HEAD_SHA" ]; then
    echo "Live head $live differs from this run's $HEAD_SHA; the newer run owns it."; return 0
  fi
  if ! jq -e '[.labels[].name] | index("review:approved")' "$tmp/pr.json" >/dev/null; then
    echo "PR #$PR no longer carries review:approved; nothing to flag."; return 0
  fi
  return 1
}

has_stamp() {
  jq -r -f "$here/select-approval-comments.jq" "$tmp/pr.json" \
    | bash "$here/approval-stamp-matches.sh" "$HEAD_SHA"
}

fetch
if stand_down; then exit 0; fi

jq -r '.comments[] | select(.author.login | test("^github-actions(\\[bot\\])?$") | not) | .body | @base64' \
  "$tmp/pr.json" > "$tmp/verdict-comments.b64"
if ! bash "$here/verdict-approve-token-matches.sh" "$HEAD_SHA" < "$tmp/verdict-comments.b64"; then
  echo "No valid verdict-approval token for $HEAD_SHA on PR #$PR; the missing stamp is an ordinary skip."
  exit 0
fi
if bash "$here/verdict-reject-token-matches.sh" "$HEAD_SHA" < "$tmp/verdict-comments.b64"; then
  echo "A reject token vetoes $HEAD_SHA on PR #$PR; the missing stamp is an ordinary skip."
  exit 0
fi

# Bounded wait for the labeled run's stamp. Re-reads everything live each pass.
elapsed=0
while true; do
  fetch
  if stand_down; then exit 0; fi
  if has_stamp; then
    echo "Approval stamp for $HEAD_SHA appeared after ${elapsed}s; nothing to flag."
    exit 0
  fi
  if [ "$elapsed" -ge "$total" ]; then break; fi
  sleep "$interval"
  elapsed=$((elapsed + interval))
done

marker="<!-- approval-stamp-missing $HEAD_SHA -->"
jq -r '.comments[].body' "$tmp/pr.json" > "$tmp/all-bodies.txt"
if ! grep -qF "$marker" "$tmp/all-bodies.txt"; then
  {
    echo "$marker"
    echo "Automerge did NOT merge PR #$PR: it carries review:approved and a valid verdict-approval"
    echo "token for head $HEAD_SHA, but no approval stamp for that head appeared within ${total}s."
    echo ""
    echo "To unstick it: remove review:approved and add it again. The labeled event re-checks the"
    echo "token and posts the stamp; automerge then merges."
  } | gh pr comment "$PR" --repo "$REPO" --body-file - || true
fi
echo "PR #$PR is approved with a valid token for $HEAD_SHA but has no approval stamp after ${total}s; failing so the check is red." >&2
exit 1
