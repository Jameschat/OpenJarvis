#!/usr/bin/env bash
# plan-commit — enforce conventional-commit prefix + Plan-Step trailer.
# Usage: scripts/plan-commit.sh <type(scope)> <plan-step-id> <message>
# Example: scripts/plan-commit.sh "feat(agents)" A3 "surface active project in brain context"
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: plan-commit <type(scope)> <plan-step-id> <message>" >&2
  exit 2
fi

prefix="$1"
step="$2"
shift 2
msg="$*"

if ! [[ "$prefix" =~ ^(feat|fix|refactor|docs|chore|test|perf)(\([a-z0-9-]+\))?$ ]]; then
  echo "error: '$prefix' is not a valid conventional-commit type(scope)" >&2
  exit 2
fi

if ! [[ "$step" =~ ^[A-Z][0-9]+$ ]]; then
  echo "error: plan-step '$step' must look like A1, B3, etc." >&2
  exit 2
fi

git commit -m "$prefix: $msg

Plan-Step: $step"
