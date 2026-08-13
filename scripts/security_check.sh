#!/usr/bin/env bash
set -euo pipefail

if find . -type f -name '.env*' -not -name '.env.example.disabled' -not -path './.git/*' | grep -q .; then
  echo 'Environment files are prohibited.' >&2
  exit 1
fi

if git grep -n -I -E '(AKIA[0-9A-Z]{16}|-----BEGIN (RSA|EC|OPENSSH|PRIVATE) KEY-----|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})' -- . ':!docs/sprints/*'; then
  echo 'Potential credential pattern found.' >&2
  exit 1
fi

if git ls-files | grep -E '(^|/)(\.env($|\.)|.*\.(sqlite3|db|log))$' | grep -v '^$'; then
  echo 'Environment files, databases, and logs must not be tracked.' >&2
  exit 1
fi

git diff --check
printf '%s\n' 'Security checks passed.'
