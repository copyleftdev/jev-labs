#!/usr/bin/env bash
# Publish to GitHub with discovery metadata. Run once, after reviewing README.md.
#
#     ./publish.sh copyleftdev/jev-labs
#
# Requires: gh auth login (already done on this machine).
set -euo pipefail
REPO="${1:?usage: publish.sh owner/name}"

DESC="Never confidently wrong: a TLA+-verified consensus kernel around TypeSafe's Jev, run through 1,680 chaos-tested pharmacy decisions with zero wrong verdicts. Film, code, and every captured call."

# GitHub allows 20 topics. Ordered by discovery value.
TOPICS=(
  typesafe-ai jev system-one
  tla-plus tlc model-checking formal-verification
  consensus byzantine-fault-tolerance distributed-systems
  rust asyncapi
  chaos-engineering deterministic-simulation property-based-testing metamorphic-testing
  llm-evaluation ai-safety calibration
  antithesis
)

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" --public --source=. --description "$DESC" --push
else
  gh repo edit "$REPO" --description "$DESC"
  git push -u origin HEAD
fi
gh repo edit "$REPO" --homepage "https://youtu.be/C_l8FI1oddE" \
  --enable-issues --enable-wiki=false
gh repo edit "$REPO" $(printf -- '--add-topic %s ' "${TOPICS[@]}")
echo
gh repo view "$REPO" --json url,description,repositoryTopics --jq '.url, .description, ([.repositoryTopics[].name] | join(" "))'
