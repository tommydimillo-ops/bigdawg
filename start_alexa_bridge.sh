#!/bin/bash
# Start the Jarvis Alexa bridge: the local listener plus a tunnel that
# lets the Alexa skill reach it.
#
# Run with:  bash ~/CampusPilot/start_alexa_bridge.sh
#
# Safe to re-run. Generates the bridge token once (into the Keychain via
# agent/secrets.py) and reuses it afterwards. Leave this running while
# you want the Dot to be able to give Jarvis tasks -- closing it takes
# the bridge down, which is also your kill switch.
set -uo pipefail
cd "$(dirname "$0")"

# The project's dependencies (keyring, and everything agent/ imports)
# live in .venv, not in system python3 -- same as "Launch Assistant.command".
if [ ! -f ".venv/bin/activate" ]; then
  echo "No .venv found in $(pwd)." >&2
  echo "This needs the project's virtualenv, the same one Launch Assistant.command uses." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! python3 -c "import keyring" 2>/dev/null; then
  echo "The virtualenv is active but 'keyring' isn't installed in it." >&2
  echo "Install it with:  pip install keyring" >&2
  exit 1
fi

PORT="${ALEXA_BRIDGE_PORT:-8765}"
CF_BIN="$HOME/.local/bin/cloudflared"

# --- 1. bridge token -------------------------------------------------
# A long random string, stored in the Keychain. This is the only thing
# standing between the public tunnel URL and an agent with your files,
# so it is generated rather than chosen, and never printed in full.
TOKEN=$(python3 -c "
from agent.secrets import get_secret
print(get_secret('ALEXA_BRIDGE_TOKEN') or '')
" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "No bridge token yet — generating one."
  TOKEN=$(python3 -c "
import secrets
from agent.secrets import set_secret
t = secrets.token_urlsafe(32)
set_secret('ALEXA_BRIDGE_TOKEN', t)
print(t)
")
  if [ -z "$TOKEN" ]; then
    echo "Could not generate or store a token. Is agent/secrets.py importable from here?" >&2
    exit 1
  fi
  echo "Stored in your Keychain as ALEXA_BRIDGE_TOKEN."
fi

# --- 2. cloudflared --------------------------------------------------
# Downloaded directly rather than via Homebrew, which isn't installed.
if [ ! -x "$CF_BIN" ] && ! command -v cloudflared >/dev/null 2>&1; then
  echo "Downloading cloudflared (one time)…"
  mkdir -p "$(dirname "$CF_BIN")"
  ARCH=$(uname -m)
  case "$ARCH" in
    arm64)  CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz" ;;
    x86_64) CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz" ;;
    *) echo "Unexpected architecture: $ARCH" >&2; exit 1 ;;
  esac
  TMP=$(mktemp -d)
  if ! curl -fsSL "$CF_URL" -o "$TMP/cf.tgz"; then
    echo "Download failed. Check your connection and try again." >&2
    exit 1
  fi
  tar -xzf "$TMP/cf.tgz" -C "$TMP" && mv "$TMP/cloudflared" "$CF_BIN" && chmod +x "$CF_BIN"
  rm -rf "$TMP"
  echo "Installed to $CF_BIN"
fi
command -v cloudflared >/dev/null 2>&1 && CF_BIN=$(command -v cloudflared)

# --- 3. listener -----------------------------------------------------
echo
echo "Starting the bridge listener on 127.0.0.1:$PORT …"
python3 -m agent.alexa_daemon "$PORT" &
LISTENER_PID=$!

cleanup() {
  echo
  echo "Shutting down…"
  kill "$LISTENER_PID" 2>/dev/null
  [ -n "${TUNNEL_PID:-}" ] && kill "$TUNNEL_PID" 2>/dev/null
  exit 0
}
trap cleanup INT TERM

# Wait for the port rather than sleeping a guessed amount.
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$LISTENER_PID" 2>/dev/null; then
    echo "The listener exited during startup — see the message above." >&2
    exit 1
  fi
  sleep 0.5
done

if ! curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "The listener never became healthy on port $PORT." >&2
  cleanup
fi
echo "Listener is up."

# --- 4. tunnel -------------------------------------------------------
# A quick tunnel: free, no Cloudflare account. The hostname is random
# and CHANGES EVERY TIME this script restarts, so the skill has to be
# updated with the new one. A named tunnel (needs a Cloudflare account
# and a domain) gives a stable hostname if that gets annoying.
echo "Opening the tunnel…"
TUNNEL_LOG=$(mktemp)
"$CF_BIN" tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

TUNNEL_URL=""
for _ in $(seq 1 60); do
  TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
  [ -n "$TUNNEL_URL" ] && break
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "The tunnel exited. Log:" >&2; tail -20 "$TUNNEL_LOG" >&2; cleanup
  fi
  sleep 1
done

if [ -z "$TUNNEL_URL" ]; then
  echo "Tunnel didn't report a URL in time. Log:" >&2; tail -20 "$TUNNEL_LOG" >&2; cleanup
fi

echo
echo "======================================================================"
echo " Jarvis Alexa bridge is live."
echo
echo " Paste these two into the Alexa skill's index.js (CONFIG block):"
echo
echo "   BRIDGE_URL:   $TUNNEL_URL"
echo "   BRIDGE_TOKEN: $TOKEN"
echo
echo " The URL changes each time you restart this script; the token does not."
echo
echo " Leave this window open. Ctrl-C stops the bridge — that is the"
echo " kill switch: with it stopped, the Dot can't reach Jarvis at all."
echo "======================================================================"
echo

wait "$LISTENER_PID"
