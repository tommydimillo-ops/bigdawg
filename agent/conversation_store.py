import json
import os

# Streamlit already serves this app on the local network (visible as
# "Network URL" when it starts), so a phone on the same Wi-Fi can already
# reach it — but by default each browser tab gets its own isolated
# st.session_state, so a phone and a Mac hitting the same server would see
# two disconnected conversations. Persisting the conversation here instead
# of relying only on session_state makes it genuinely shared: whichever
# device opens the page sees the real, current conversation, not a stale
# per-device copy.
CONVERSATION_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/conversation.json")


def load_conversation():
    if not os.path.exists(CONVERSATION_FILE):
        return []
    with open(CONVERSATION_FILE, "r") as file:
        return json.load(file)


def save_conversation(messages):
    os.makedirs(os.path.dirname(CONVERSATION_FILE), exist_ok=True)
    tmp_file = f"{CONVERSATION_FILE}.tmp"
    with open(tmp_file, "w") as file:
        json.dump(messages, file, indent=2)
    os.replace(tmp_file, CONVERSATION_FILE)


def clear_conversation():
    save_conversation([])
