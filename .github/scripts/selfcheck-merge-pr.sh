#!/usr/bin/env bash
# Self-check for merge-pr.sh, run by the workflow's gate-selfcheck job on every PR. Drives the
# script against a fake `gh` that logs each merge call (token + args) and asserts: the normal
# token is used first, the workflows-permission refusal triggers exactly one retry with the
# fallback token and the same flags, no fallback without a secret, and the secret is never printed.
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

cat > "$work/bin/gh" <<'FAKE'
#!/usr/bin/env bash
if [ "$1 $2" = "pr view" ]; then echo "$SIM_LIVE"; exit 0; fi
if [ "$1 $2" = "pr merge" ]; then
  echo "$GH_TOKEN $*" >> "$SIM_LOG"
  case "$SIM_MODE" in
    ok) exit 0 ;;
    refuse)
      if [ "$GH_TOKEN" = "$SIM_PAT" ]; then exit 0; fi
      echo 'refusing to allow a GitHub App to create or update workflow `.github/workflows/ci.yml` without `workflows` permission (mergePullRequest)' >&2
      exit 1 ;;
    refuse_all)
      echo 'refusing to allow a GitHub App to create or update workflow `.github/workflows/ci.yml` without `workflows` permission (mergePullRequest)' >&2
      exit 1 ;;
    fail) echo 'Base branch was modified. Review and try the merge again.' >&2; exit 1 ;;
  esac
fi
exit 0
FAKE
chmod +x "$work/bin/gh"

sha="1111111111111111111111111111111111111111"
other="2222222222222222222222222222222222222222"
normal="normal-token-value"
pat="secret-pat-value"
fail=0
ran=0
INTENDED=6

# run_case <name> <mode> <with_pat 0|1> <live sha> <attempts> <want rc> <want merge calls> <want pat calls>
run_case() {
  local name="$1" mode="$2" with_pat="$3" live="$4" attempts="$5" want_rc="$6" want_calls="$7" want_pat="$8"
  export SIM_LOG="$work/log" SIM_MODE="$mode" SIM_LIVE="$live" SIM_PAT="$pat"
  : > "$SIM_LOG"
  local out rc
  if [ "$with_pat" = "1" ]; then
    out=$(PATH="$work/bin:$PATH" PR=7 REPO=o/r HEAD_SHA="$sha" GH_TOKEN="$normal" AUTOMERGE_WORKFLOW_PAT="$pat" \
          MERGE_ATTEMPTS="$attempts" MERGE_SLEEP=0 bash "$here/merge-pr.sh" 2>&1)
  else
    out=$(PATH="$work/bin:$PATH" PR=7 REPO=o/r HEAD_SHA="$sha" GH_TOKEN="$normal" AUTOMERGE_WORKFLOW_PAT="" \
          MERGE_ATTEMPTS="$attempts" MERGE_SLEEP=0 bash "$here/merge-pr.sh" 2>&1)
  fi
  rc=$?
  ran=$((ran + 1))
  local calls pat_calls
  calls=$(wc -l < "$SIM_LOG" | tr -d ' ')
  pat_calls=$(grep -c "^$pat " "$SIM_LOG" || true)
  [ "$rc" = "$want_rc" ] || { echo "FAIL [$name]: exit $rc, want $want_rc"; fail=1; }
  [ "$calls" = "$want_calls" ] || { echo "FAIL [$name]: $calls merge calls, want $want_calls"; fail=1; }
  [ "$pat_calls" = "$want_pat" ] || { echo "FAIL [$name]: $pat_calls fallback calls, want $want_pat"; fail=1; }
  if printf '%s' "$out" | grep -qF "$pat" || printf '%s' "$out" | grep -qF "$normal"; then
    echo "FAIL [$name]: a token value was printed"; fail=1
  fi
  if [ "$calls" -gt 0 ] && ! grep -v '^$' "$SIM_LOG" | grep -q -- "--squash --delete-branch --match-head-commit $sha"; then
    echo "FAIL [$name]: merge flags missing"; fail=1
  fi
  if [ "$want_pat" -gt 0 ]; then
    printf '%s' "$out" | grep -qF "AUTOMERGE_WORKFLOW_PAT fallback" || { echo "FAIL [$name]: fallback line not logged"; fail=1; }
  fi
}

#        name                      mode        pat live    att rc calls pat
run_case "normal merge"            ok          1   "$sha"   3   0  1     0
run_case "refusal, fallback ok"    refuse      1   "$sha"   3   0  2     1
run_case "refusal, no secret"      refuse      0   "$sha"   2   1  2     0
run_case "fallback also refused"   refuse_all  1   "$sha"   2   1  3     1
run_case "transient failure"       fail        1   "$sha"   2   1  2     0
run_case "head moved"              fail        1   "$other" 3   0  1     0

if [ "$ran" != "$INTENDED" ]; then
  echo "FAIL ran $ran cases, intended $INTENDED"; fail=1
fi
if [ "$fail" != "0" ]; then exit 1; fi
echo "merge-pr.sh self-check passed: all $ran cases resolved correctly"
