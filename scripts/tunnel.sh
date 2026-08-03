#!/usr/bin/env bash
# Give the Beeline app a public HTTPS URL so judges can scan a QR and open /join
# on their own phones. Zero account needed.
#
# Usage:  bash scripts/tunnel.sh            # tunnels the default app port 8113
#         PORT=8113 bash scripts/tunnel.sh
#
# It prints a public https URL. Open <that-url>/ on the projector for the map;
# the QR on the map auto-points at <that-url>/join because it is built from the
# page's own origin. Judges scan it, land on the phone check-in, and their dot
# appears live on the map.
#
# Order of preference:
#   1) cloudflared  (most reliable for a demo; install: brew install cloudflared)
#   2) ssh + localhost.run  (no install, no account; needs only ssh)
set -euo pipefail
PORT="${PORT:-8113}"

echo "Tunneling http://localhost:${PORT} to a public URL ..."
echo "Keep this window open for the whole demo. Ctrl-C ends the tunnel."
echo

if command -v cloudflared >/dev/null 2>&1; then
  echo "Using cloudflared (watch for the https://<something>.trycloudflare.com line):"
  echo
  exec cloudflared tunnel --url "http://localhost:${PORT}"
fi

echo "cloudflared not found; falling back to localhost.run over ssh."
echo "Watch for the https://<something>.lhr.life URL in the output below."
echo "(If it asks to trust a host key, answer yes.)"
echo
# -R 80:... forwards the remote port to our local app; localhost.run prints the URL.
exec ssh -o StrictHostKeyChecking=accept-new -R "80:localhost:${PORT}" nokey@localhost.run
