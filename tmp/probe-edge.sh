#!/usr/bin/env bash
# Read-only reconnaissance on historian: who serves the public kimb.dev edge?
set -u

echo "=== DNS resolution ==="
for h in kimb.dev blog.kimb.dev borges.kimb.dev kjb.kimb.dev; do
  printf '%-22s %s\n' "$h" "$(getent hosts "$h" 2>/dev/null | head -1 | awk '{print $1}')"
done

echo
echo "=== Caddy active? ==="
systemctl is-active caddy
systemctl is-enabled caddy

echo
echo "=== Caddyfile: which .kimb.dev hostnames ==="
sudo grep -oE '[a-z0-9.-]+\.kimb\.dev' /etc/caddy/Caddyfile 2>/dev/null | sort -u

echo
echo "=== Caddyfile location / managed by nix? ==="
sudo ls -la /etc/caddy/Caddyfile
readlink -f /etc/caddy/Caddyfile

echo
echo "=== TLS certs held for kimb.dev ==="
sudo find /var/lib/caddy -maxdepth 6 -type d -name '*kimb.dev' 2>/dev/null | head -20

echo
echo "=== listening on 80/443 here? ==="
sudo ss -lntp 2>/dev/null | grep -E ':(80|443)\s' | head -10

echo
echo "=== reverse-proxy entry in historian's registry bucket ==="
grep -n 'reverse-proxy' /home/kimb/systems-flake/services/default.nix | head -10
