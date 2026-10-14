#!/usr/bin/env bash
# Turn each fixtures/<name> into its own git repository under repos/<name>.
# Fixed author and timestamps make the commit SHAs identical on every machine.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf repos && mkdir repos
for src in fixtures/*/; do
  name=$(basename "$src")
  cp -R "$src" "repos/$name"
  (cd "repos/$name" && find . -name __pycache__ -prune -exec rm -rf {} + \
    && git init -q -b main && git add -A \
    && GIT_AUTHOR_DATE="2026-10-01T12:00:00Z" GIT_COMMITTER_DATE="2026-10-01T12:00:00Z" \
       git -c user.name="Fixture Author" -c user.email="fixtures@changeblast.local" commit -q -m "Initial import" \
    && echo "$name $(git rev-parse --short HEAD)")
done
