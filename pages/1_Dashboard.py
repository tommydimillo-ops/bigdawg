from datetime import datetime

import streamlit as st

from agent.audit import recent_actions
from agent.autonomy import describe_level
from agent.brain import TOOLS
from agent.cancellation import request_cancel
from agent.cowork_gateway import describe_status as describe_cowork_status
from agent.cowork_gateway import status as cowork_status
from agent.execution_history import get_by_id, get_recent
from agent.execution_state import list_active
from agent.jarvis_state import get_state, is_busy
from agent.memory import MemoryType, list_all
from agent.permissions import permission_label
from agent.scheduled_tasks import list_tasks
from agent.skills.registry import list_skills
from config.settings import settings

st.set_page_config(page_title="Jarvis Dashboard", page_icon="📊", layout="wide")

st.title("📊 Jarvis Dashboard")
st.caption(f"Live status — {datetime.now().strftime('%A, %B %d, %Y — %I:%M %p')}")

# --- Live execution -----------------------------------------------------
# JarvisState is filesystem-backed, so this view also sees work owned by
# the menu-bar app or scheduler. The local registry is used only to enrich
# the card with iteration details when Streamlit owns the request itself.
shared_state = get_state()
local_by_id = {state.request_id: state for state in list_active()}
local_state = local_by_id.get(shared_state.active_request_id)

st.subheader("⚡ Live execution")
if is_busy() and shared_state.active_request_id:
    with st.container(border=True):
        cols = st.columns(5)
        cols[0].metric("Status", shared_state.status.replace("_", " "))
        cols[1].metric("Current tool", shared_state.current_tool or "—")
        cols[2].metric("Plan", shared_state.plan_progress or "—")
        cols[3].metric(
            "Elapsed",
            f"{local_state.duration_seconds:.1f}s" if local_state else "Another interface",
        )
        with cols[4]:
            st.caption("")
            if st.button(
                "Cancel",
                key=f"cancel_{shared_state.active_request_id}",
                icon=":material/stop_circle:",
            ):
                if request_cancel(shared_state.active_request_id):
                    st.success("Cancellation requested. The current tool will finish safely first.")
                else:
                    st.warning("That request is no longer active.")
        if shared_state.current_task:
            st.caption(shared_state.current_task)
        if shared_state.confirmation_pending:
            st.caption(f"Waiting on confirmation for `{shared_state.current_tool}`")
        if local_state and local_state.tools_executed:
            st.caption("Tools so far: " + ", ".join(f"`{t}`" for t in local_state.tools_executed))
else:
    st.caption("Nothing in flight right now.")

st.divider()

# --- Recent executions ---------------------------------------------------
# Persistent (agent/execution_history.py), unlike the "Live execution"
# section above -- these survive past the request finishing and past a
# process restart, bounded to the most recent settings.execution_history_
# limit entries. Sanitized before it was ever written to disk, so nothing
# shown here needs to be filtered again at display time.
st.subheader("🗂️ Recent executions")
recent_executions = get_recent()
if recent_executions:
    st.table([
        {
            "Time": datetime.fromtimestamp(r.timestamp).strftime("%b %d, %I:%M %p"),
            "Request": r.request_summary,
            "Status": r.status,
            "Duration": f"{r.duration_seconds:.1f}s" if r.duration_seconds is not None else "—",
            "Model": r.model or "—",
            "Tools used": ", ".join(r.tools_used) if r.tools_used else "—",
        }
        for r in recent_executions
    ])

    selected_id = st.selectbox(
        "Inspect a specific execution",
        options=[r.request_id for r in recent_executions],
        format_func=lambda rid: next(
            (f"{r.request_summary} ({r.status}, {datetime.fromtimestamp(r.timestamp).strftime('%I:%M %p')})"
             for r in recent_executions if r.request_id == rid),
            rid,
        ),
    )
    detail = get_by_id(selected_id)
    if detail:
        with st.container(border=True):
            dcols = st.columns(4)
            dcols[0].metric("Status", detail.status)
            dcols[1].metric("Duration", f"{detail.duration_seconds:.1f}s" if detail.duration_seconds is not None else "—")
            dcols[2].metric("Tool calls", detail.tool_count)
            dcols[3].metric("Memories used", detail.memories_retrieved_count)
            if detail.plan_created:
                st.caption(f"Plan: {detail.plan_completed_steps}/{detail.plan_total_steps} steps completed")
            if detail.tools_used:
                st.caption("Tools: " + ", ".join(f"`{t}`" for t in detail.tools_used))
            if detail.errors:
                st.error("\n".join(detail.errors))
            if detail.autonomy_level is not None:
                st.caption(f"Autonomy level at the time: {detail.autonomy_level}")
else:
    st.caption("No completed executions recorded yet.")

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

    st.subheader("🧩 Skills")
    st.caption(
        "Structured workflow guidance Jarvis can attach to a matching request "
        "(agent/delegation.py) -- never a second way to run code or call a "
        "tool. Whatever a skill leads to still goes through the same "
        "permission/autonomy/confirmation checks as anything else."
    )
    installed_skills = list_skills()
    if installed_skills:
        for skill in sorted(installed_skills, key=lambda s: s.name):
            status = "✅ enabled" if skill.enabled else "⏸️ disabled"
            with st.expander(f"{skill.name} (v{skill.version}) — {status}"):
                st.caption(skill.description)
                st.markdown(f"**Risk level:** {skill.risk_level.value}")
                if skill.capabilities:
                    st.markdown("**Capabilities:** " + ", ".join(skill.capabilities))
                if skill.required_tools:
                    st.markdown("**Tools it typically uses:** " + ", ".join(f"`{t}`" for t in skill.required_tools))
    else:
        st.caption("No skills installed.")

    recent_delegated = [r for r in recent_executions if r.delegation_destination == "claude_skill"]
    if recent_delegated:
        st.markdown("**Recent skill executions:**")
        st.table([
            {
                "Time": datetime.fromtimestamp(r.timestamp).strftime("%b %d, %I:%M %p"),
                "Skill": r.delegated_skill,
                "Request": r.request_summary,
                "Status": r.status,
            }
            for r in recent_delegated[:10]
        ])

    st.caption(f"Cowork integration: {cowork_status().value} — {describe_cowork_status()}")
