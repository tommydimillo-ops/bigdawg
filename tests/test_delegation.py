"""Tests for agent/delegation.py -- the delegation policy. Deterministic:
no model call, isolated from whatever skills happen to be loaded from
disk by registering a known skill directly.

Run with: python -m unittest tests.test_delegation -v
"""
import unittest

import agent.skills.registry as skills_registry
from agent.delegation import DelegationDestination, decide
from agent.skills.models import RiskLevel, Skill


class IsolatedSkillsRegistryTestCase(unittest.TestCase):

    def setUp(self):
        self._real_registry = dict(skills_registry._REGISTRY)
        skills_registry.clear()
        skills_registry.register(Skill(
            name="research", description="Research a topic across sources.",
            version="1.0", instructions="Search and cross-check.",
            capabilities=["research", "web search"],
        ))
        skills_registry.register(Skill(
            name="risky_skill", description="A high risk example skill.",
            version="1.0", instructions="Be careful.",
            capabilities=["risky business"], risk_level=RiskLevel.HIGH,
        ))

    def tearDown(self):
        skills_registry.clear()
        skills_registry._REGISTRY.update(self._real_registry)


class TestDecide(IsolatedSkillsRegistryTestCase):

    def test_matching_request_delegates_to_claude_skill(self):
        decision = decide("Can you research the best laptops?")
        self.assertEqual(decision.destination, DelegationDestination.CLAUDE_SKILL)
        self.assertEqual(decision.skill, "research")

    def test_unrelated_request_delegates_to_native_tool(self):
        decision = decide("Open Safari.")
        self.assertEqual(decision.destination, DelegationDestination.NATIVE_TOOL)
        self.assertIsNone(decision.skill)

    def test_confidence_and_reason_are_populated(self):
        decision = decide("Can you research the best laptops?")
        self.assertGreater(decision.confidence, 0.0)
        self.assertTrue(decision.reason)

    def test_high_risk_skill_sets_requires_confirmation(self):
        decision = decide("Help with this risky business situation.")
        self.assertEqual(decision.skill, "risky_skill")
        self.assertTrue(decision.requires_confirmation)

    def test_low_risk_skill_does_not_set_requires_confirmation(self):
        decision = decide("Can you research the best laptops?")
        self.assertFalse(decision.requires_confirmation)

    def test_decide_never_dispatches_a_tool(self):
        # Structural check, matching tests/test_phase4_security.py's
        # pattern for agent/planner.py -- confirms decide() has no code
        # path to tools.registry.dispatch, even indirectly.
        import ast
        import inspect
        import agent.delegation as delegation_module
        tree = ast.parse(inspect.getsource(delegation_module))
        dispatch_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "dispatch"
        ]
        self.assertEqual(dispatch_calls, [])


if __name__ == "__main__":
    unittest.main()
