"""Tests for tools/schemas/skills.py -- view_skills/enable_skill/
disable_skill, exercised through the real tools.registry.dispatch() path,
matching tests/test_registry.py's pattern for the rest of the tool set.

Run with: python -m unittest tests.test_skills_tools -v
"""
import unittest

import tools.schemas  # noqa: F401 -- populates the registry
import agent.skills.registry as skills_registry
from agent.skills.models import Skill
from tools import registry


class IsolatedSkillsRegistryTestCase(unittest.TestCase):

    def setUp(self):
        self._real_registry = dict(skills_registry._REGISTRY)
        skills_registry.clear()
        skills_registry.register(Skill(
            name="research", description="Research a topic across sources.",
            version="1.0", instructions="Search and cross-check.",
            capabilities=["research"],
        ))

    def tearDown(self):
        skills_registry.clear()
        skills_registry._REGISTRY.update(self._real_registry)


class TestToolsAreRegistered(unittest.TestCase):

    def test_view_skills_is_registered(self):
        self.assertIn("view_skills", registry.all_names())

    def test_enable_disable_are_registered(self):
        self.assertIn("enable_skill", registry.all_names())
        self.assertIn("disable_skill", registry.all_names())

    def test_view_skills_is_read_only(self):
        self.assertEqual(registry.permission_level("view_skills"), 0)


class TestViewSkills(IsolatedSkillsRegistryTestCase):

    def test_lists_installed_skills(self):
        result = registry.dispatch("view_skills", {})
        self.assertIn("research", result)
        self.assertIn("enabled", result)

    def test_no_skills_installed_message(self):
        skills_registry.clear()
        result = registry.dispatch("view_skills", {})
        self.assertIn("No skills installed", result)

    def test_shows_disabled_status(self):
        skills_registry.set_enabled("research", False)
        result = registry.dispatch("view_skills", {})
        self.assertIn("disabled", result)


class TestEnableDisableSkill(IsolatedSkillsRegistryTestCase):

    def test_disable_then_enable(self):
        result = registry.dispatch("disable_skill", {"name": "research"})
        self.assertIn("Disabled", result)
        self.assertFalse(skills_registry.get("research").enabled)

        result = registry.dispatch("enable_skill", {"name": "research"})
        self.assertIn("Enabled", result)
        self.assertTrue(skills_registry.get("research").enabled)

    def test_unknown_skill_name(self):
        result = registry.dispatch("disable_skill", {"name": "not_a_real_skill"})
        self.assertIn("No skill named", result)

    def test_missing_name_argument(self):
        result = registry.dispatch("enable_skill", {})
        self.assertIn("required", result)


if __name__ == "__main__":
    unittest.main()
