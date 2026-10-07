#!/usr/bin/env bash
# Build the historian closure on historian using the same evaluator the repo's
# `deploy` alias uses (colmena apply --evaluator streaming).
set -uo pipefail
cd "$HOME/systems-flake"

echo "=== what extra commits does this deploy pick up? ==="
git log --oneline 862a59e..HEAD --no-merges | head -20

echo
echo "=== BUILD ONLY, streaming evaluator (no activation) ==="
nix develop --command bash -c 'colmena build --evaluator streaming --on historian' 2>&1 | tail -30

echo
echo "=== BUILD EXIT: $? ==="
