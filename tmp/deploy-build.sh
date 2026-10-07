#!/usr/bin/env bash
# Pull the systems-flake change on historian and BUILD (not activate) the
# historian closure, so a build failure shows up before anything is switched.
set -euo pipefail

cd "$HOME/systems-flake"

echo "=== before ==="
git rev-parse --short HEAD
git status --porcelain | head -5

echo
echo "=== pull ==="
git pull --ff-only

echo
echo "=== after ==="
git rev-parse --short HEAD

echo
echo "=== is the kjb entry present? ==="
grep -n "kjb" services/default.nix | head -5
ls -la hosts/historian/kjb.nix

echo
echo "=== colmena availability inside the devshell ==="
nix develop --command bash -c 'command -v colmena && colmena --version'

echo
echo "=== BUILD ONLY (no activation) ==="
nix develop --command bash -c 'colmena build --on historian' 2>&1 | tail -25

echo
echo "=== BUILD DONE ==="
