"""plan-b9 item 1 -- GATING for the live code-execution exposure.

plan-b8's report found `run_python` is a registered tool the main agent can
call today, independent of `coding_agent_enabled`. plan-b9's scoping (driving
the real executor gate) measured that at the default autonomy of 4 it ran
with NO confirmation from every entry point -- including the scheduler and
Alexa, which have nobody watching -- because its ToolSpec left
`unattended_allowed` at the default True and the level-2 check passes at
autonomy >= 3. Everything b6-b8 hardened sat behind a disabled flag; this did
not.

These tests use the REAL executor gate (`agent.executor._run_tool`) and the
real registry, and stub nothing for the cases that must be blocked -- so a
pass means the tool was genuinely not dispatched, not merely that a flag is
set. Where a case must run, the real (sandboxed, harness-redirected)
`run_python` executes.

Run with: python -m unittest tests.test_code_execution_gating -v
"""
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.autonomy as autonomy
import agent.executor as executor
from agent.autonomy import Decision, ExecutionContext, should_request_confirmation
from agent.request_context import RequestContext
from agent.scheduled_tasks import list_tasks
from tools import registry

ALL_SOURCES = ("chat", "voice", "telegram", "alexa", "scheduled", "agent_worker", "model_inferred", "a_source_added_later")
NON_INTERACTIVE = {"scheduled", "agent_worker", "alexa"}
CODE_AND_TRIGGER_TOOLS = ("run_python", "delegate_parallel_tasks", "schedule_task")


def _run(tool, tool_input, source, level):
    autonomy._pending.clear()  # the confirmation ledger keys on (tool, input): a prior "ask" would read as confirmed
    context = RequestContext.create("gating test", source=source)
    context.autonomy_level = level
    return executor._run_tool(tool, tool_input, source=source, context=context, state=None)


class _ClearLedger(unittest.TestCase):
    def setUp(self):
        autonomy._pending.clear()
        self.addCleanup(autonomy._pending.clear)


class TestRunPythonIsNeverUnattended(_ClearLedger):

    def test_the_toolspec_says_it_is_not_unattended_allowed(self):
        self.assertFalse(registry.unattended_allowed("run_python"))

    def test_a_scheduled_task_can_never_run_code_at_any_autonomy_level(self):
        for level in range(0, 5):
            with self.subTest(level=level):
                result = _run("run_python", {"code": "print('SHOULD-NOT-RUN')"}, "scheduled", level)
                self.assertIn("can't run unattended", result)
                self.assertNotIn("SHOULD-NOT-RUN", result)
                self.assertNotIn("Ran in an isolated sandbox", result)

    def test_alexa_can_never_run_code_at_any_autonomy_level(self):
        for level in range(0, 5):
            with self.subTest(level=level):
                result = _run("run_python", {"code": "print('SHOULD-NOT-RUN')"}, "alexa", level)
                self.assertIn("isn't possible unattended", result)
                self.assertNotIn("Ran in an isolated sandbox", result)

    def test_KNOWN_GAP_the_unattended_flag_is_only_enforced_for_the_scheduler_source(self):
        # NOT closed by plan-b9, found while testing it, and pinned here so it
        # cannot be forgotten: executor._run_tool's `unattended_allowed` hard
        # gate fires only for source == "scheduled" (executor.py), not for the
        # other non-interactive sources (alexa, agent_worker). run_python is
        # covered for Alexa by the always-confirm rule above, and agent_worker
        # never reaches _run_tool for a registered tool (coworker agents run
        # their own loops) -- but every OTHER unattended_allowed=False tool is
        # not: the whole computer_* family (screen/keyboard/mouse) RUNS from
        # Alexa at the default autonomy, i.e. a television line could drive the
        # keyboard. Widening the gate to _NON_INTERACTIVE_SOURCES is the fix;
        # it changes behavior for computer_* from Alexa, so it is left as a
        # decision (see the plan-b9 report). When fixed, this test should fail:
        # flip it to assert "can't run unattended".
        autonomy._pending.clear()
        context = RequestContext.create("gating test", source="alexa")
        context.autonomy_level = 4
        with patch.object(registry, "dispatch", return_value="__DISPATCHED__"):
            result = executor._run_tool("computer_click", {"x": 1, "y": 1}, source="alexa", context=context, state=None)
        self.assertEqual(result, "__DISPATCHED__")
        self.assertFalse(registry.unattended_allowed("computer_click"))

    def test_ambient_voice_asks_first_even_at_the_highest_autonomy(self):
        result = _run("run_python", {"code": "print('SHOULD-NOT-RUN')"}, "voice", 4)
        self.assertIn("want your OK first", result)
        self.assertNotIn("Ran in an isolated sandbox", result)

    def test_legitimate_interactive_use_still_works_at_the_default_autonomy(self):
        # Not over-broad: typed chat and Telegram (a deliberate, owner-only
        # channel) at autonomy 4 still run code, and it really executes.
        for source in ("chat", "telegram"):
            with self.subTest(source=source):
                result = _run("run_python", {"code": "print(6 * 7)"}, source, 4)
                self.assertIn("Ran in an isolated sandbox", result)
                self.assertIn("42", result)


class TestAmbientVoiceAlwaysConfirmsCodeDelegationAndScheduling(_ClearLedger):

    def test_voice_confirms_and_alexa_is_denied_for_each_tool_at_every_autonomy_level(self):
        for tool in CODE_AND_TRIGGER_TOOLS:
            for level in range(0, 5):
                with self.subTest(tool=tool, level=level):
                    self.assertEqual(
                        should_request_confirmation(tool, level, ExecutionContext(source="voice")), Decision.CONFIRM,
                    )
                    self.assertEqual(
                        should_request_confirmation(tool, level, ExecutionContext(source="alexa")), Decision.DENY,
                    )

    def test_the_tools_that_were_already_guarded_still_are(self):
        for tool in ("add_reminder", "open_browser", "consult_coworker_agent"):
            with self.subTest(tool=tool):
                self.assertEqual(
                    should_request_confirmation(tool, 4, ExecutionContext(source="voice")), Decision.CONFIRM,
                )

    def test_delegate_parallel_tasks_now_matches_consult_coworker_agent(self):
        # The inconsistency the scoping found: both dispatch to the same
        # coworker agents, but only one was guarded from ambient voice.
        for source in ("voice", "alexa"):
            with self.subTest(source=source):
                self.assertEqual(
                    should_request_confirmation("delegate_parallel_tasks", 4, ExecutionContext(source=source)),
                    should_request_confirmation("consult_coworker_agent", 4, ExecutionContext(source=source)),
                )

    def test_ordinary_tools_are_not_swept_up(self):
        for tool in ("get_weather", "list_upcoming", "add_calendar_event", "get_system_status"):
            with self.subTest(tool=tool):
                self.assertEqual(
                    should_request_confirmation(tool, 4, ExecutionContext(source="alexa")), Decision.ALLOW,
                )


class TestScheduleTaskAlwaysNeedsConfirmation(_ClearLedger):
    """The model must never create its own persistent trigger unconfirmed --
    from a prompt-injected web page, a misheard line, a scheduled run, or a
    coworker agent."""

    def test_never_allowed_from_any_source_at_any_autonomy_level(self):
        for source in ALL_SOURCES:
            for level in range(0, 5):
                with self.subTest(source=source, level=level):
                    decision = should_request_confirmation("schedule_task", level, ExecutionContext(source=source))
                    self.assertNotEqual(decision, Decision.ALLOW)

    def test_interactive_sources_confirm_and_non_interactive_sources_are_denied(self):
        for source in ALL_SOURCES:
            with self.subTest(source=source):
                decision = should_request_confirmation("schedule_task", 4, ExecutionContext(source=source))
                self.assertEqual(decision, Decision.DENY if source in NON_INTERACTIVE else Decision.CONFIRM)

    def test_through_the_real_executor_chat_asks_first_and_creates_nothing(self):
        before = len(list_tasks())
        result = _run("schedule_task", {"prompt": "say hi", "time_of_day": "09:00"}, "chat", 4)
        self.assertIn("want your OK first", result)
        self.assertEqual(len(list_tasks()), before)

    def test_after_the_user_confirms_the_same_call_goes_through_once(self):
        tool_input = {"prompt": "say hi", "time_of_day": "09:00"}
        context = RequestContext.create("gating test", source="chat")
        context.autonomy_level = 4
        first = executor._run_tool("schedule_task", tool_input, source="chat", context=context, state=None)
        self.assertIn("want your OK first", first)
        with patch.object(registry, "dispatch", return_value="__DISPATCHED__") as dispatch:
            second = executor._run_tool("schedule_task", tool_input, source="chat", context=context, state=None)
            self.assertEqual(second, "__DISPATCHED__")
            dispatch.assert_called_once()

    def test_a_scheduled_task_cannot_schedule_another_task(self):
        before = len(list_tasks())
        result = _run("schedule_task", {"prompt": "x", "time_of_day": "09:00"}, "scheduled", 4)
        self.assertNotIn("Scheduled", result)
        self.assertEqual(len(list_tasks()), before)

    def test_alexa_cannot_create_a_scheduled_task(self):
        before = len(list_tasks())
        result = _run("schedule_task", {"prompt": "x", "time_of_day": "09:00"}, "alexa", 4)
        self.assertIn("isn't possible unattended", result)
        self.assertEqual(len(list_tasks()), before)


if __name__ == "__main__":
    unittest.main()
