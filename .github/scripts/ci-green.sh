#!/bin/sh
# Exit 0 iff a successful CI run from a real push to main of this very
# repository exists for the commit given as $1. Shared by the deploy and
# publish workflows so the CI-green check lives in one place.
set -e

SHA="$1"
: "${SHA:?usage: ci-green.sh <commit-sha>}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN must be set}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"

curl -fsS -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/ci.yml/runs?head_sha=$SHA&event=push&branch=main&status=success&per_page=5" \
  | jq -e --arg repo "$GITHUB_REPOSITORY" \
    '[.workflow_runs[]
      | select(.conclusion == "success"
        and .event == "push"
        and .head_branch == "main"
        and .head_repository.full_name == $repo)
    ] | length > 0' > /dev/null
