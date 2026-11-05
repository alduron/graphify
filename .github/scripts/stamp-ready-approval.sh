#!/usr/bin/env bash
# Usage (all input via env): PR REPO HEAD_SHA   [GITHUB_OUTPUT optional]
#
# Stamps an approval that PREDATES the current event. review:approved can land while the PR is
# still a draft; the `labeled` run then skips every job (all are gated on draft == false), so no
# `<!-- ap:approved-sha:... -->` stamp is ever posted, and when the PR is later marked ready
# automerge finds the label but no stamp and strands the PR
# (Caveat_ApprovalLabelOnADraftNeverGetsStampedSoAutomergeSkipsSilently; api PR 861, runs
# 42061342553 and 42061392144). The approval job calls this on a non-draft opened / reopened /
# ready_for_review event to run the labeled path's logic for a label that is already there.
#
# Nothing is weakened relative to the labeled path:
#   - the label is read LIVE (the event payload is a snapshot from before any later label);
#   - the head is read LIVE and must equal HEAD_SHA, so a stamp can only name the exact head;
#   - a verdict-approve token for that head must exist in a NON-bot comment, with no reject
#     token for it, using the same two matcher scripts. The labeled path skips this check when the
#     labeler differs from the PR author; there is no labeler in this event, so the token is ALWAYS
#     required here (stricter, never looser);
#   - the stamp is posted as the workflow's own bot identity, once per head sha.
# Every "no" outcome exits 0 (a missing token is a pending human step, not a broken build);
# automerge re-verifies the same token and stamp itself and is what decides the merge.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if [ "$(gh pr view "$PR" --repo "$REPO" --json labels --jq 'any(.labels[].name; . == "review:approved")')" != "true" ]; then
  echo "PR #$PR does not carry review:approved; nothing to stamp."
  exit 0
fi

live_head="$(gh pr view "$PR" --repo "$REPO" --json headRefOid --jq '.headRefOid')"
if [ "$live_head" != "$HEAD_SHA" ]; then
  echo "PR #$PR head is $live_head, not $HEAD_SHA; a newer event covers that head. Not stamping."
  exit 0
fi

gh pr view "$PR" --repo "$REPO" --json comments \
     --jq '.comments[] | select(.author.login | test("^github-actions(\\[bot\\])?$") | not) | .body | @base64' \
     > "$tmp/verdict-comments.b64"
if ! bash "$here/verdict-approve-token-matches.sh" "$HEAD_SHA" < "$tmp/verdict-comments.b64"; then
  echo "PR #$PR carries review:approved but no verdict-approval token for $HEAD_SHA; not stamping."
  exit 0
fi
if bash "$here/verdict-reject-token-matches.sh" "$HEAD_SHA" < "$tmp/verdict-comments.b64"; then
  echo "PR #$PR has a reject token for $HEAD_SHA; not stamping."
  exit 0
fi

if gh pr view "$PR" --repo "$REPO" --json comments --jq "$(cat "$here/select-approval-comments.jq")" \
     | bash "$here/approval-stamp-matches.sh" "$HEAD_SHA"; then
  echo "PR #$PR already has an approval stamp for $HEAD_SHA; nothing to do."
  exit 0
fi

gh pr comment "$PR" --repo "$REPO" --body "<!-- ap:approved-sha:$HEAD_SHA -->"
echo "stamped $HEAD_SHA on PR #$PR (review:approved predated this event)."
