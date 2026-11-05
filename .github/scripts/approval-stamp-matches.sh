#!/usr/bin/env bash
# Usage: approval-stamp-matches.sh <head-sha>
#
# Reads candidate PR comment bodies on stdin (one per line, already filtered by the caller to
# comments authored by github-actions[bot] - see automerge's call site in ci.yml) and exits 0
# if any of them stamps the given head sha as approved via an "<!-- ap:approved-sha:<sha> -->"
# marker, 1 otherwise.
#
# Deliberately a single fixed-string grep, no field splitting, after an earlier version used
# `grep -oE 'ap:approved-sha:[0-9a-f]{40}' | cut -d: -f2` to pull the sha out of the match. The
# match "ap:approved-sha:<sha>" has THREE colon-separated fields (ap / approved-sha / <sha>), so
# -f2 returned the literal text "approved-sha" every time, never a real sha - automerge's sha
# check then always failed and automerge never merged anything again (fails closed, not a
# security hole, but still wrong, and a workflow-only diff hid it: CI on the introducing PR was
# green because the broken line only runs on a LATER labeled event). Matching the whole literal
# string instead of extracting and comparing a substring removes that bug class entirely.
# test-approval-stamp-match (same ci.yml) runs this exact script against fixtures on every PR,
# so a regression here fails the PR that introduces it.
set -euo pipefail
head_sha="$1"
grep -qF "ap:approved-sha:${head_sha}"
