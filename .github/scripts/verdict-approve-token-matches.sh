#!/usr/bin/env bash
# Usage: verdict-approve-token-matches.sh <head-sha>
#
# Reads, on stdin, one BASE64-ENCODED PR comment body per line -- the caller passes
# `gh pr view --json comments --jq '.comments[].body | @base64'`, NEVER raw `.body` text -- and
# exits 0 if any ONE comment's entire body, after trimming leading/trailing whitespace, is
# EXACTLY the literal token "<!-- ap:verdict-approve:<head-sha> -->" and nothing else, 1
# otherwise.
#
# WHOLE-BODY, NOT SUBSTRING. The predecessor script (same name, see git history) did
# `grep -qF "<!-- ap:verdict-approve:$head_sha -->"` against the raw comment stream: the token
# anywhere in any comment counted. That is exactly how it manufactured its own bypass: this same
# repo's own auto-posted rejection message prints the required token with the LIVE head sha
# already interpolated (it has to, to show the reviewer what to paste), so the bot's own
# "here is the format" message was itself a valid approval the instant it posted -- label
# without a token, get rejected with a message CONTAINING the token for the still-current head,
# re-apply the label, and the check now finds that token inside its own prior rejection. The same
# substring hole let explicit rejection prose through too: "I am REJECTING this. Do NOT post
# <!-- ap:verdict-approve:H --> until the blocker is fixed." resolves ALLOWED under substring
# matching, because the literal characters are present, never mind the sentence around them.
# Both holes close at the root once a comment only counts when its ENTIRE body, nothing more, is
# the token: the rejection message is many lines, not one; the rejection prose is a sentence, not
# a token. Findings go in a separate comment from the token.
#
# This DELIBERATELY REVERSES the predecessor's own stated design (its header argued a
# blockquoted or fenced token should still count, since a 40-char live sha cannot be typed by
# accident): under whole-body matching a blockquoted token's body is "> <!-- ... -->" and a
# fenced token's body is a fenced block, neither of which equals the bare token string, so
# NEITHER COUNTS ANY MORE. That reversal is intended, not a regression: substring matching was
# the vulnerability, and whole-body matching has no shape left to exploit, which is worth more
# than accommodating a quoting style.
#
# WHY BASE64, NOT RAW TEXT ON STDIN. `gh pr view --json comments --jq '.comments[].body'`
# prints one line per comment ONLY when a body itself contains no newline. A real comment is
# usually multi-line, so that raw stream concatenates every comment into one undifferentiated
# blob with no recoverable boundary: two adjacent single-line comments and one two-line comment
# are indistinguishable on it, so a whole-body check against that stream cannot tell "this one
# comment's whole body is the token" from "the token is merely adjacent, in the stream, to
# another comment's unrelated text" -- which would silently reopen the exact substring hole this
# script exists to close, one comment boundary at a time. Base64-encoding each body before it
# ever reaches a newline-delimited pipe removes the ambiguity structurally: a body's own embedded
# newlines become ordinary base64 characters, not line breaks, so every newline THIS SCRIPT sees
# on stdin is a genuine comment boundary and nothing else.
#
# See the fixture table in .github/workflows/ci.yml for the cases this script is proven against,
# including the adjacent-comments proof described above.
set -euo pipefail
head_sha="$1"
approve_token="<!-- ap:verdict-approve:${head_sha} -->"

while IFS= read -r encoded || [ -n "$encoded" ]; do
  encoded="${encoded%$'\r'}"   # tolerate a stray CR on the wire; base64 has none of its own
  [ -z "$encoded" ] && continue
  body="$(printf '%s' "$encoded" | base64 -d 2>/dev/null)" || continue
  # Trim leading/trailing whitespace only (spaces, tabs, newlines) -- a token followed by the
  # trailing newline GitHub adds to nearly every real comment, or sitting on its own line inside
  # otherwise-blank padding, still counts; a token with any OTHER text anywhere in the same body
  # -- before it, after it, or wrapped in a blockquote/fence -- does not, because that text
  # survives the trim and breaks the equality check below.
  #
  # The set is spelled out as EXPLICIT ASCII bytes, not bash's "[:space:]" bracket class, because
  # that class is resolved by the CURRENT LOCALE's iswspace() table, not a fixed alphabet: under
  # en_US.UTF-8 or C.utf8 -- the realistic ubuntu-latest default -- it follows Unicode's
  # White_Space property and includes U+00A0 NO-BREAK SPACE, but under C, POSIX, or an unset
  # locale it does not. A comment body ending in a trailing NBSP therefore trimmed to the bare
  # token (ALLOWED) under one of those locales and failed to trim (BLOCKED) under the other, with
  # the gate's actual verdict depending on an environment variable this script never set. Writing
  # the whitespace set out here removes the dependency at its root: it is one line to read, true
  # in every locale by construction, and needs no caller to remember to pin one. U+00A0 and every
  # other non-ASCII "space" codepoint are deliberately left OUT of this set on purpose -- a token
  # followed by one is content, not padding, and does not match, in every locale now, not just
  # some of them. See the locale-determinism fixture group in .github/workflows/ci.yml.
  ws=$' \t\n\r\f\v'
  trimmed="$body"
  trimmed="${trimmed#"${trimmed%%[!$ws]*}"}"
  trimmed="${trimmed%"${trimmed##*[!$ws]}"}"
  if [ "$trimmed" = "$approve_token" ]; then
    exit 0
  fi
done
exit 1
