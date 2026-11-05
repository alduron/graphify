#!/usr/bin/env bash
# Usage: latest-check-run.sh <job-name>
#
# Reads JSON on stdin: the --slurp'd array of pages from the "list check runs for a commit" API
# -- the caller passes `gh api repos/<r>/commits/<sha>/check-runs?per_page=100 --paginate --slurp`
# with NO --jq on the gh call itself (see (1) below for why). Prints "<status> <conclusion>" for
# the most recent (highest check-run id) check-run named $1 whose conclusion is not "skipped", or
# "absent absent" if no such check-run exists.
#
# Extracted from automerge's "Resolve live check state for the head sha" step in
# .github/workflows/ci.yml (cli PR 393) after this exact logic produced two live bugs in one PR
# review cycle -- a per-page jq aggregate and a cascade-skip self-poisoning -- both caught by
# reading, not by a test, while its siblings (verdict-approve-token-matches.sh,
# approval-stamp-matches.sh) already had fixture self-checks. See ci.yml's "Self-check
# latest-check-run.sh" step for the fixture cases this is proven against.
#
# (1) --paginate --slurp WITH NO --jq ON THE gh CALL. An aggregate (max_by, length, ...) inside
# `gh api --jq` is evaluated PER PAGE, not across the whole paginated result, and prints one line
# per page; a caller that reads a single line (e.g. `read -r a b <<< "$row"`) silently takes
# whichever page happened to come first and discards the rest
# (Caveat_GhPaginateEvaluatesAJqAggregatePerPageNotAcrossPages). Proved by forcing per_page=2 on a
# real sha: the per-page form returned four lines for one call. --slurp merges every page into one
# array before any filter runs, which is why this script's own filter walks `.[].check_runs[]`
# (an array of pages, each with its own check_runs) rather than `.check_runs[]` (one page) -- and
# why --slurp cannot be combined with --jq on the gh call (gh rejects that combination), so the
# filter has to live here instead of in the gh invocation.
#
# (2) SKIPPED conclusions are excluded outright, not merely the calling workflow run's own
# entries. GitHub posts a check-run for every job even when its `if:` is false, so a bare
# "exclude this run's own entries" filter leaves an EARLIER run's cascade-skip in play: push
# (success), label (skip), unlabel, relabel with no new push -- exactly ci.yml's own documented
# recovery flow -- and a run-id filter alone would see [success, skip] and pick the newer skip,
# which carries zero information about the code. Excluding "skipped" itself, from any run, is
# what makes the answer correct rather than merely less wrong.
#
# A run-id exclusion (there used to be one here) is dead weight once (2) holds: every entry this
# script could ever see that was POSTED BY THE CALLING RUN ITSELF is, by construction, a skip --
# ci.yml only reaches this script for a job whose result IN THIS RUN was "skipped" -- so the skip
# filter alone already removes it. Proven by the self-check's "current run's own skip is already
# excluded by the skip filter alone" case, which passes with no run-id parameter at all.
set -euo pipefail
job_name="$1"
jq -r --arg name "$job_name" '
    [ .[].check_runs[]
      | select(.name == $name)
      | select(.conclusion != "skipped") ]
    | if length == 0 then "absent absent"
      else (max_by(.id) | "\(.status) \(.conclusion)") end'
