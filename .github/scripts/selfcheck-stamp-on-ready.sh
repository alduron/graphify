#!/usr/bin/env bash
# Self-check for stamp-ready-approval.sh and approval-stamp-missing.sh, run by the workflow's
# self-check job on every PR. Drives both scripts against a fake `gh` (backed by a JSON file and
# the real jq filters the scripts pass) and asserts which comments get posted and the exit code.
# Cases: ready_for_review stamps only with a live label AND a valid sha-bound token for the live
# head; automerge's missing-stamp branch fails (with one marker comment per head) only in the
# label + valid token + no stamp state and keeps exit 0 for every other cause.
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

cat > "$work/bin/gh" <<'FAKE'
#!/usr/bin/env bash
D="$SIM_DIR"
if [ "$1 $2" = "pr view" ]; then
  shift 3
  jq_f=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo|--json) shift ;;
      --jq) jq_f="$2"; shift ;;
    esac
    shift
  done
  [ -n "$jq_f" ] || jq_f="."
  jq -r "$jq_f" "$D/pr.json" | tr -d '\r'
elif [ "$1 $2" = "pr comment" ]; then
  shift 3
  body=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) shift ;;
      --body) body="$2"; shift ;;
      --body-file) if [ "$2" = "-" ]; then body="$(cat)"; else body="$(cat "$2")"; fi; shift ;;
    esac
    shift
  done
  printf '%s\n---\n' "$body" >> "$D/posted.log"
  t="$(mktemp)"
  jq --arg b "$body" '.comments += [{"author":{"login":"github-actions"},"body":$b}]' "$D/pr.json" > "$t" && mv "$t" "$D/pr.json"
else
  echo "fake gh: unsupported: $*" >&2
  exit 99
fi
FAKE
# Fake sleep: never waits; runs the function named by $SIM_ON_SLEEP (if set) so a case can change
# the PR mid-wait.
cat > "$work/bin/sleep" <<'FAKE'
#!/usr/bin/env bash
[ -n "${SIM_ON_SLEEP:-}" ] && "$SIM_ON_SLEEP"
exit 0
FAKE
chmod +x "$work/bin/gh" "$work/bin/sleep"
export STAMP_WAIT_TOTAL=30 STAMP_WAIT_INTERVAL=10
export PATH="$work/bin:$PATH"
export PR=7 REPO=o/r
sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
other=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export HEAD_SHA=$sha
fail=0

mk() { # mk <label yes|no> <token approve|none|reject|botonly> [live head]
  export SIM_DIR="$work/sim"
  unset SIM_ON_SLEEP
  rm -rf "$SIM_DIR"; mkdir -p "$SIM_DIR"; : > "$SIM_DIR/posted.log"
  local labels='[]'; [ "$1" = yes ] && labels='[{"name":"review:approved"}]'
  local a="<!-- ap:verdict-approve:$sha -->" r="<!-- ap:verdict-reject:$sha -->"
  local c
  case "$2" in
    approve) c=$(jq -n --arg a "$a" '[{"author":{"login":"alduron"},"body":$a}]') ;;
    reject) c=$(jq -n --arg a "$a" --arg r "$r" '[{"author":{"login":"alduron"},"body":$a},{"author":{"login":"alduron"},"body":$r}]') ;;
    botonly) c=$(jq -n --arg a "$a" '[{"author":{"login":"github-actions"},"body":$a}]') ;;
    *) c='[{"author":{"login":"alduron"},"body":"looks fine"}]' ;;
  esac
  jq -n --argjson l "$labels" --argjson c "$c" --arg h "${3:-$sha}" '{state:"OPEN",labels:$l,comments:$c,headRefOid:$h}' > "$SIM_DIR/pr.json"
}

expect() { # expect <name> <want-rc> <got-rc> <want-stamps> <want-markers>
  local s m
  s=$(grep -c "ap:approved-sha:$sha" "$SIM_DIR/posted.log" || true)
  m=$(grep -c "approval-stamp-missing $sha" "$SIM_DIR/posted.log" || true)
  if [ "$2" = "$3" ] && [ "$s" = "$4" ] && [ "$m" = "$5" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1 (rc=$3 want $2; stamps=$s want $4; markers=$m want $5)"; fail=1
  fi
}

stamp() { bash "$here/stamp-ready-approval.sh" >/dev/null 2>&1; echo $?; }
missing() { bash "$here/approval-stamp-missing.sh" >/dev/null 2>&1; echo $?; }

mk yes approve;  rc=$(stamp);   expect "ready: label + valid token -> stamp posted" 0 "$rc" 1 0
rc=$(stamp);                    expect "ready: re-run does not duplicate the stamp" 0 "$rc" 1 0
mk yes none;     rc=$(stamp);   expect "ready: label, no token -> no stamp" 0 "$rc" 0 0
mk yes botonly;  rc=$(stamp);   expect "ready: bot-authored token does not count" 0 "$rc" 0 0
mk yes reject;   rc=$(stamp);   expect "ready: reject token vetoes" 0 "$rc" 0 0
mk yes approve $other; rc=$(stamp); expect "ready: head moved -> no stamp" 0 "$rc" 0 0
mk no approve;   rc=$(stamp);   expect "ready: no label -> no-op" 0 "$rc" 0 0
mk yes approve;  rc=$(missing); expect "automerge: label + token, no stamp -> marker + non-zero" 1 "$rc" 0 1
rc=$(missing);                  expect "automerge: marker posted once per head" 1 "$rc" 0 1
mk yes none;     rc=$(missing); expect "automerge: label, no token, no stamp -> exit 0" 0 "$rc" 0 0
mk yes reject;   rc=$(missing); expect "automerge: reject token, no stamp -> exit 0" 0 "$rc" 0 0
# Wait cases: something changes while automerge is waiting (the fake sleep runs the hook).
edit() { local t="$SIM_DIR/t"; jq "$@" "$SIM_DIR/pr.json" > "$t" && mv "$t" "$SIM_DIR/pr.json"; }
hook_stamp() { edit --arg b "<!-- ap:approved-sha:$sha -->" '.comments += [{author:{login:"github-actions"},body:$b}]'; }
hook_merged() { edit '.state="MERGED"'; }
hook_unlabel() { edit '.labels=[]'; }
hook_headmove() { edit --arg h "$other" '.headRefOid=$h'; }
export -f edit hook_stamp hook_merged hook_unlabel hook_headmove
export sha other

mk yes approve; export SIM_ON_SLEEP=hook_stamp;    rc=$(missing)
expect "automerge: stamp appears mid-wait -> exit 0, no marker" 0 "$rc" 0 0
mk yes approve; export SIM_ON_SLEEP=hook_merged;   rc=$(missing)
expect "automerge: PR merged mid-wait -> exit 0, no marker" 0 "$rc" 0 0
mk yes approve; export SIM_ON_SLEEP=hook_unlabel;  rc=$(missing)
expect "automerge: label removed mid-wait -> exit 0, no marker" 0 "$rc" 0 0
mk yes approve; export SIM_ON_SLEEP=hook_headmove; rc=$(missing)
expect "automerge: head moves mid-wait -> exit 0, no marker" 0 "$rc" 0 0
mk yes approve $other; rc=$(missing)
expect "automerge: live head differs from payload -> exit 0, no marker" 0 "$rc" 0 0
unset SIM_ON_SLEEP
exit $fail
