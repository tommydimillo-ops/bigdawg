"""Confirms voice naturally triggers skill delegation with no voice-
specific wiring -- agent.voice_session.run_request() calls
execute_task_stream() exactly like every other interface, and delegation
(agent/delegation.py) is decided inside execute_task_stream() itself, so
a voice request that matches a skill gets it attached automatically.
Only the network call is mocked (matching this project's established
policy -- see tests/test_planner.py's docstring).

Run with: python -m unittest tests.test_voice_skill_integration -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.skills.registry as skills_registry
import agent.voice_session as voice_session
from agent.skills.models import Skill


class TestVoiceTriggersSkillDelegation(unittest.TestCase):

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")

        self._real_registry = dict(skills_registry._REGISTRY)
        skills_registry.clear()
        skills_registry.register(Skill(
            name="research", description="Research a topic across sources.",
            version="1.0", instructions="Search multiple sources, cross-check facts.",
            capabilities=["research", "web search"],
        ))

    def tearDown(self):
        for path in (
            execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp",
            jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp",
        ):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file
        jarvis_state.STATE_FILE = self._real_state_file
        skills_registry.clear()
        skills_registry._REGISTRY.update(self._real_registry)

    @patch("agent.executor.claude_client")
    def test_voice_request_matching_a_skill_attaches_it(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        stream.text_stream = iter(["Sure, researching that now."])
        stream.get_final_message.return_value = response
        mock_client.messages.stream.return_value = stream

        result = voice_session.run_request("Can you research the best laptops under $1000?")

        self.assertEqual(result.state.selected_skill, "research")
        self.assertEqual(result.state.delegation_destination, "claude_skill")

    @patch("agent.executor.claude_client")
    def test_voice_request_not_matching_any_skill_uses_native_tool(self, mock_client):
        response = MagicMock(stop_reason="end_turn")
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        stream.text_stream = iter(["Four."])
        stream.get_final_message.return_value = response
        mock_client.messages.stream.return_value = stream

        result = voice_session.run_request("What is 2+2?")

        self.assertIsNone(result.state.selected_skill)
        self.assertEqual(result.state.delegation_destination, "native_tool")


if __name__ == "__main__":
    unittest.main()
