#!/usr/bin/env bash
# Usage: verdict-reject-token-matches.sh <head-sha>
#
# Reads, on stdin, one BASE64-ENCODED PR comment body per line -- the caller passes
# `gh pr view --json comments --jq '.comments[].body | @base64'`, the same stream
# verdict-approve-token-matches.sh reads -- and exits 0 if any ONE comment's entire body, after
# trimming leading/trailing whitespace, is EXACTLY the literal token
# "<!-- ap:verdict-reject:<head-sha> -->" and nothing else, 1 otherwise.
#
# THE EXPLICIT VETO. An approve token proves one reviewer looked and said yes; it says nothing
# about a second reviewer who looks afterward and finds a real blocker. Before this script
# existed, that second reviewer's rejection was pure prose with zero mechanical effect -- nothing
# here parses prose by design (Decision_TheMergeGateProvesApprovalWithAShaBoundTokenNotByParsing
# Prose) -- so an honest approve token for head H, once posted, stayed live through every later
# automerge run on H no matter what anyone wrote afterward. Two independent reviewers is this
# team's actual practice, so a reviewer with no way to retract someone ELSE's still-live approval
# is a real gap, not a hypothetical one. This script is that lever: a reject token for the live
# head blocks the merge regardless of any approve token also present for that same head.
#
# MONOTONIC AND ORDER-INDEPENDENT, ON PURPOSE. This script and
# verdict-approve-token-matches.sh are each evaluated independently against the full, current
# comment list on every run; the merge gate requires an approve token for the live head AND no
# reject token for the live head, with neither check caring which comment came first. There is
# no "latest verdict wins" semantic anywhere in this pair of scripts -- comment ORDER was the
# substrate of several of the eight holes in the prose parser both token scripts replaced
# (Decision_TheMergeGateProvesApprovalWithAShaBoundTokenNotByParsingProse), so reintroducing any
# notion of ordering here would reopen the same shape of bug for the veto instead of fixing it.
# A reject token for the live head blocks a merge whether it was posted before or after an
# approve token for that same head; an approve token posted after a reject token does not
# un-block it either. The only way to clear a posted reject token is to edit or delete that
# GitHub comment, which removes it from what this script reads on its next invocation.
#
# THAT RETRACTION IS VISIBLE-ACTION-ONLY, NOT IDENTITY-CHECKED -- say this plainly rather than
# implying a protection that is not there. Editing or deleting a comment leaves a trace (GitHub's
# edit history, or the gap where the comment used to be) in place of the silence a veto that was
# simply never posted would have left, which is the real and useful half of this. But every agent
# and the human owner authenticate to GitHub as the SAME login
# (Caveat_SharedGhAuthLetsAnyWorkerSelfApprove), so "the reviewer's own stale comment" is not a
# real distinction here: the actor who posted a reject token and the actor who could delete it
# again are structurally the same actor.
# This script does NOT prevent a self-approving actor from clearing a veto cast against their own
# diff -- it only makes doing so leave evidence instead of nothing. Closing that gap for real needs
# a reviewer credential distinct from the author's, which is an infrastructure decision outside
# what a CI script can enforce.
#
# STALE REJECTIONS DO NOT BLOCK A NEW HEAD, for the same structural reason staleness cannot
# silently approve: the token names a commit. A reject token bound to a sha that is no longer
# the PR's head (an old blocker, since fixed by a later push) does not match this script's
# head_sha argument and so does not block the new head -- the same sha-binding
# verdict-approve-token-matches.sh already relies on to make an approval of commit A structurally
# incapable of covering a later commit B.
#
# Shares verdict-approve-token-matches.sh's whole-body-only matching and its base64 wire format
# for the same two reasons that script's own header gives in full: substring matching on raw text
# let a rejection token typed into ordinary prose, or printed inside an unrelated automated
# message, count as a veto nobody intended to cast; and a raw, newline-delimited comment stream
# cannot be split back into per-comment boundaries, so two adjacent comments could otherwise
# combine into a token neither one actually posted alone.
set -euo pipefail
head_sha="$1"
reject_token="<!-- ap:verdict-reject:${head_sha} -->"

while IFS= read -r encoded || [ -n "$encoded" ]; do
  encoded="${encoded%$'\r'}"   # tolerate a stray CR on the wire; base64 has none of its own
  [ -z "$encoded" ] && continue
  body="$(printf '%s' "$encoded" | base64 -d 2>/dev/null)" || continue
  # Explicit ASCII whitespace set, not bash's locale-dependent "[:space:]" class -- see
  # verdict-approve-token-matches.sh's own header for why: that class includes U+00A0 NO-BREAK
  # SPACE under a UTF-8 locale but not under C/POSIX, which made this trim's result depend on the
  # runner's ambient locale instead of being fixed by the code itself.
  ws=$' \t\n\r\f\v'
  trimmed="$body"
  trimmed="${trimmed#"${trimmed%%[!$ws]*}"}"
  trimmed="${trimmed%"${trimmed##*[!$ws]}"}"
  if [ "$trimmed" = "$reject_token" ]; then
    exit 0
  fi
done
exit 1
