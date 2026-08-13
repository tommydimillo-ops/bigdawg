from datetime import datetime

import streamlit as st

from agent.audit import recent_actions
from agent.brain import TOOLS
from agent.permissions import permission_label
from agent.scheduled_tasks import list_tasks
from database.memory import get_memory

st.set_page_config(page_title="Jarvis Dashboard", page_icon="📊", layout="wide")

st.title("📊 Jarvis Dashboard")
st.caption(f"Live status — {datetime.now().strftime('%A, %B %d, %Y — %I:%M %p')}")

memory = get_memory()
notes = memory.get("notes", [])
if isinstance(notes, str):
    notes = [notes]
lessons = memory.get("lessons", [])
tasks = list_tasks()
actions = recent_actions(200)
today = datetime.now().strftime("%Y-%m-%d")
actions_today = [a for a in actions if a["timestamp"].startswith(today)]

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Tools available", len(TOOLS))
col2.metric("Standing rules", len(lessons))
col3.metric("Facts remembered", len(notes))
col4.metric("Scheduled tasks", len(tasks))
col5.metric("Actions logged today", len(actions_today))

st.divider()

left, right = st.columns([3, 2])

with left:
    st.subheader("Recent activity")
    if actions:
        st.table([
            {
                "Time": a["timestamp"].split("T")[1],
                "Tool": a["tool"],
                "Permission": a.get("permission", ""),
                "Result": (a["result"][:80] + "…") if len(a["result"]) > 80 else a["result"],
            }
            for a in reversed(actions[-15:])
        ])
    else:
        st.caption("No actions logged yet — nothing's happened in a conversation yet.")

    st.subheader("Scheduled tasks")
    if tasks:
        st.table([
            {
                "Time": t["time_of_day"],
                "Task": t["prompt"],
                "Status": "enabled" if t.get("enabled", True) else "disabled",
                "Last ran": t.get("last_run_date") or "never",
            }
            for t in tasks
        ])
        st.caption("Only actually run if `python -m agent.scheduler_daemon` is running.")
    else:
        st.caption("Nothing scheduled. Ask Jarvis to schedule something, or use schedule_task.")

with right:
    st.subheader("Standing rules")
    if lessons:
        for rule in lessons:
            st.markdown(f"- {rule}")
    else:
        st.caption("No corrections learned yet.")

    st.subheader("Remembered facts")
    if notes:
        for note in notes:
            st.markdown(f"- {note}")
    else:
        st.caption("Nothing remembered yet.")

    st.subheader("Tools by permission level")
    by_level = {}
    for tool in TOOLS:
        label = permission_label(tool["name"])
        by_level.setdefault(label, []).append(tool["name"])
    for label in sorted(by_level):
        with st.expander(f"{label} — {len(by_level[label])} tools"):
            for name in by_level[label]:
                st.markdown(f"- `{name}`")
