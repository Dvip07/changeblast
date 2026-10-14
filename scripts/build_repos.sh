#!/usr/bin/env bash
# Turn each fixtures/<name> into its own git repository under $REPOS_DIR/<name> (default: repos/).
# Fixed author and timestamps make the commit SHAs identical on every machine.
# Only fixture folders are replaced: GitHub clones in the same directory are left alone.
set -euo pipefail
cd "$(dirname "$0")/.."
REPOS_DIR="${REPOS_DIR:-repos}"
mkdir -p "$REPOS_DIR"
for src in fixtures/*/; do
  name=$(basename "$src")
  rm -rf "${REPOS_DIR:?}/$name"
  cp -R "$src" "$REPOS_DIR/$name"
  (cd "$REPOS_DIR/$name" && find . -name __pycache__ -prune -exec rm -rf {} + \
    && git init -q -b main && git add -A \
    && GIT_AUTHOR_DATE="2026-10-01T12:00:00Z" GIT_COMMITTER_DATE="2026-10-01T12:00:00Z" \
       git -c user.name="Fixture Author" -c user.email="fixtures@changeblast.local" commit -q -m "Initial import" \
    && echo "$name $(git rev-parse --short HEAD)")
done
