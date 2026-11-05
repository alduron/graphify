# Selects candidate approval-stamp comment bodies from a `gh pr view --json comments` payload,
# restricted to the identity record-approval-sha posts as. Shared between automerge (loaded via
# `--jq "$(cat this file)"`) and test-approval-stamp-match (loaded via local `jq -f`), so a
# regression here is caught by the self-test on the PR that introduces it, not discovered live.
#
# Matches BOTH "github-actions" and "github-actions[bot]": gh's GraphQL comments field reports
# this identity's login as "github-actions" with no "[bot]" suffix, while the REST API and
# github.actor use "github-actions[bot]". An earlier version matched only the "[bot]" spelling,
# so .author.login never matched anything gh pr view actually returns, the candidate list was
# always empty, and automerge always skipped - fails closed, but bricks every merge (umbrella PR
# 377, labeled for real, 2026-10-18). The identity restriction itself stays: a human or agent
# sharing the owner's gh auth cannot post as either spelling of this bot.
.comments[] | select(.author.login | test("^github-actions(\\[bot\\])?$")) | .body
