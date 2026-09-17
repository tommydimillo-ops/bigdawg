#!/bin/bash
# Connect Telegram to Jarvis — one-time setup, then starts the listener.
# Run with:  bash ~/CampusPilot/connect_telegram.sh
set -uo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env"
touch "$ENV_FILE"

# Safe to re-run: strip any previous TELEGRAM_ lines before adding fresh ones.
grep -v '^TELEGRAM_' "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null && mv "$ENV_FILE.tmp" "$ENV_FILE"

echo "Paste your Telegram bot token (from BotFather) and press Enter."
echo "(It won't be shown on screen as you paste it — that's normal.)"
read -r -s TOKEN
echo
if [ -z "$TOKEN" ]; then
  echo "No token entered — nothing was changed. Run this again when you have it."
  exit 1
fi
echo "TELEGRAM_BOT_TOKEN=$TOKEN" >> "$ENV_FILE"

echo
echo "Now open Telegram on your phone, find your bot, and send it any message (e.g. \"hi\")."
read -r -p "Once you've sent it, come back here and press Enter... " _

CHAT_ID=$(python3 - "$TOKEN" <<'PYEOF'
import sys, json, urllib.request

token = sys.argv[1]
try:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15) as r:
        data = json.load(r)
except Exception as e:
    print(f"ERROR: could not reach Telegram ({e})", file=sys.stderr)
    sys.exit(1)

if not data.get("ok", False):
    print(f"ERROR: Telegram rejected the token: {data.get('description', data)}", file=sys.stderr)
    sys.exit(1)

results = data.get("result", [])
if not results:
    print("ERROR: no messages found yet from you to the bot. Make sure you actually sent it a message, then run this script again.", file=sys.stderr)
    sys.exit(1)

chat = results[-1]["message"]["chat"]["id"]
print(chat)
PYEOF
)
STATUS=$?

if [ $STATUS -ne 0 ] || [ -z "$CHAT_ID" ]; then
  echo
  echo "Couldn't find your chat ID yet — see the error above."
  echo "Fix it and just run this script again (it's safe to re-run)."
  exit 1
fi

echo "TELEGRAM_OWNER_CHAT_ID=$CHAT_ID" >> "$ENV_FILE"
echo "TELEGRAM_ENABLED=true" >> "$ENV_FILE"

echo
echo "Config saved. Your Telegram chat ID is $CHAT_ID."
echo "Starting the Telegram listener in the background..."

if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi

mkdir -p logs
nohup python -m agent.telegram_daemon >> logs/telegram_daemon.out.log 2>&1 &
DAEMON_PID=$!
sleep 2

if kill -0 "$DAEMON_PID" 2>/dev/null; then
  echo
  echo "Done. The listener is running (pid $DAEMON_PID)."
  echo "Text your bot \"hi\" on Telegram now — you should get a reply in a few seconds."
  echo
  echo "This listener stops if you close this Terminal window or restart your Mac."
  echo "To stop it manually any time: pkill -f agent.telegram_daemon"
else
  echo
  echo "The listener didn't stay running. Check logs/telegram_daemon.out.log for the error and paste it back to me."
fi
