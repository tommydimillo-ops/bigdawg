from datetime import datetime

import streamlit as st

from agent.audit import recent_actions
from agent.autonomy import describe_level
from agent.brain import TOOLS
from agent.execution_state import list_active
from agent.memory import MemoryType, list_all
from agent.permissions import permission_label
from agent.scheduled_tasks import list_tasks
from config.settings import settings

st.set_page_config(page_title="Jarvis Dashboard", page_icon="📊", layout="wide")

st.title("📊 Jarvis Dashboard")
st.caption(f"Live status — {datetime.now().strftime('%A, %B %d, %Y — %I:%M %p')}")

# --- Live execution -----------------------------------------------------
# Only sees requests running in *this* process -- the Streamlit app and
# the menu-bar (voice) app are separate OS processes, so a voice
# request's live state won't show up here, and vice versa. Usually empty
# (a request typically finishes before a human reloads this page); genuine
# and live when non-empty.
active_executions = list_active()

st.subheader("⚡ Live execution")
if active_executions:
    for state in active_executions:
        with st.container(border=True):
            cols = st.columns(4)
            cols[0].metric("Model", state.selected_model or "selecting…")
            cols[1].metric("Iteration", f"{state.iteration}/{state.max_iterations}")
            cols[2].metric("Elapsed", f"{state.duration_seconds:.1f}s")
            cols[3].metric(
                "Status",
                "⏳ awaiting confirmation" if state.confirmation_pending
                else ("🛑 cancelled" if state.cancelled else "🔄 running"),
            )
            if state.confirmation_pending:
                st.caption(f"Waiting on confirmation for: `{state.pending_confirmation_tool}`")
            if state.plan:
                st.markdown("**Plan progress:**")
                st.text(state.plan.progress_text())
            if state.tools_executed:
                st.caption("Tools so far: " + ", ".join(f"`{t}`" for t in state.tools_executed))
else:
    st.caption("Nothing in flight right now — reload while a request is actively running to see it live.")

st.divider()

# Reads through the unified memory system (agent/memory/), not the old
# raw "notes"/"lessons" keys directly -- those are frozen at whatever
# they held at the one-time migration and no longer get updated, since
# agent.lessons/agent.patterns/agent.memory_agent now write through here
# instead.
all_memories = list_all()
lessons = [m for m in all_memories if m.type == MemoryType.LESSON]
facts = [m for m in all_memories if m.type == MemoryType.FACT]
tasks = list_tasks()
actions = recent_actions(200)
today = datetime.now().strftime("%Y-%m-%d")
actions_today = [a for a in actions if a["timestamp"].startswith(today)]

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Tools available", len(TOOLS))
col2.metric("Standing rules", len(lessons))
col3.metric("Facts remembered", len(facts))
col4.metric("Scheduled tasks", len(tasks))
col5.metric("Actions logged today", len(actions_today))
col6.metric("Total memories", len(all_memories))

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

    st.subheader("Recent memory activity")
    # Sorted by whichever is more recent: when it was created, or when it
    # was last retrieved/touched (e.g. by a relevance search matching a
    # request). Never shows content that failed the memory safety filter
    # -- that content was never stored in the first place, so there's
    # nothing sensitive here to accidentally expose.
    recently_touched = sorted(
        all_memories,
        key=lambda m: max(m.last_accessed or 0, m.updated_at),
        reverse=True,
    )[:10]
    if recently_touched:
        st.table([
            {
                "Type": m.type.value,
                "Content": (m.content[:80] + "…") if len(m.content) > 80 else m.content,
                "Confidence": m.confidence.value,
                "Importance": m.importance.value,
                "Last touched": datetime.fromtimestamp(
                    max(m.last_accessed or 0, m.updated_at)
                ).strftime("%b %d, %I:%M %p"),
            }
            for m in recently_touched
        ])
    else:
        st.caption("No memory activity yet.")

with right:
    st.subheader("Standing rules")
    if lessons:
        for rule in lessons:
            st.markdown(f"- {rule.content}")
    else:
        st.caption("No corrections learned yet.")

    st.subheader("Remembered facts")
    if facts:
        for fact in facts:
            st.markdown(f"- {fact.content}")
    else:
        st.caption("Nothing remembered yet.")

    st.subheader("Memory categories")
    by_type = {}
    for memory in all_memories:
        by_type[memory.type.value] = by_type.get(memory.type.value, 0) + 1
    if by_type:
        for type_name in sorted(by_type):
            st.markdown(f"- **{type_name}**: {by_type[type_name]}")
    else:
        st.caption("No memories yet.")

    st.subheader("Context budget")
    st.caption(
        f"Up to **{settings.context_memory_budget}** relevance-ranked "
        "pattern memories can be injected into a given request's system "
        "prompt (standing rules are always included in full, separately "
        "-- not subject to this budget)."
    )

    st.subheader("Autonomy")
    st.metric("Current level", settings.autonomy_level)
    st.caption(describe_level(settings.autonomy_level))
    pending_now = [s for s in active_executions if s.confirmation_pending]
    if pending_now:
        st.warning(
            f"⏳ {len(pending_now)} action(s) currently awaiting confirmation: "
            + ", ".join(f"`{s.pending_confirmation_tool}`" for s in pending_now)
        )
    else:
        st.caption("No action is currently awaiting confirmation.")
    st.caption(
        "This never affects the hard-gated tools (confirm_login, "
        "send_email, computer_confirm_action) or unattended/scheduled "
        "restrictions -- those are unconditional at every level."
    )

    st.subheader("Tools by permission level")
    by_level = {}
    for tool in TOOLS:
        label = permission_label(tool["name"])
        by_level.setdefault(label, []).append(tool["name"])
    for label in sorted(by_level):
        with st.expander(f"{label} — {len(by_level[label])} tools"):
            for name in by_level[label]:
                st.markdown(f"- `{name}`")
