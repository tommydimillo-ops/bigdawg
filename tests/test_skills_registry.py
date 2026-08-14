"""Tests for agent/skills/registry.py -- the single source of truth for
installed skills, same design philosophy as tests/test_registry.py
(tools.registry).

Run with: python -m unittest tests.test_skills_registry -v
"""
import unittest

import agent.skills.registry as skills_registry
from agent.skills.models import RiskLevel, Skill


def _skill(name="test_skill", **overrides):
    defaults = dict(
        name=name,
        description="A test skill for registry tests.",
        version="1.0",
        instructions="Do the test thing.",
        capabilities=["testing"],
    )
    defaults.update(overrides)
    return Skill(**defaults)


class IsolatedSkillsRegistryTestCase(unittest.TestCase):
    """Skills registered by these tests must never leak into other test
    files that import agent.skills (or worse, the real app) -- snapshot
    and restore the whole registry around each test."""

    def setUp(self):
        self._real_registry = dict(skills_registry._REGISTRY)
        skills_registry.clear()

    def tearDown(self):
        skills_registry.clear()
        skills_registry._REGISTRY.update(self._real_registry)


class TestRegisterAndGet(IsolatedSkillsRegistryTestCase):

    def test_register_and_get(self):
        skills_registry.register(_skill())
        self.assertIsNotNone(skills_registry.get("test_skill"))

    def test_get_unknown_returns_none(self):
        self.assertIsNone(skills_registry.get("not_a_real_skill"))

    def test_register_rejects_invalid_skill(self):
        bad = _skill(name="", description="")
        with self.assertRaises(ValueError):
            skills_registry.register(bad)
        self.assertIsNone(skills_registry.get(""))

    def test_reregistering_the_same_name_overwrites(self):
        # Unlike tools.registry (static, never reloaded), skills are
        # expected to be reloadable -- editing a skill file and reloading
        # should pick up the change, not raise a duplicate-name error.
        skills_registry.register(_skill(description="version one"))
        skills_registry.register(_skill(description="version two"))
        self.assertEqual(skills_registry.get("test_skill").description, "version two")

    def test_unregister_removes_it(self):
        skills_registry.register(_skill())
        self.assertTrue(skills_registry.unregister("test_skill"))
        self.assertIsNone(skills_registry.get("test_skill"))

    def test_unregister_unknown_returns_false(self):
        self.assertFalse(skills_registry.unregister("not_a_real_skill"))


class TestListSkills(IsolatedSkillsRegistryTestCase):

    def test_list_all(self):
        skills_registry.register(_skill(name="a"))
        skills_registry.register(_skill(name="b", enabled=False))
        self.assertEqual(len(skills_registry.list_skills()), 2)

    def test_list_enabled_only(self):
        skills_registry.register(_skill(name="a"))
        skills_registry.register(_skill(name="b", enabled=False))
        enabled = skills_registry.list_skills(enabled_only=True)
        self.assertEqual([s.name for s in enabled], ["a"])


class TestSetEnabled(IsolatedSkillsRegistryTestCase):

    def test_disable_and_enable(self):
        skills_registry.register(_skill())
        self.assertTrue(skills_registry.set_enabled("test_skill", False))
        self.assertFalse(skills_registry.get("test_skill").enabled)
        self.assertTrue(skills_registry.set_enabled("test_skill", True))
        self.assertTrue(skills_registry.get("test_skill").enabled)

    def test_set_enabled_unknown_returns_false(self):
        self.assertFalse(skills_registry.set_enabled("not_a_real_skill", True))


class TestSearch(IsolatedSkillsRegistryTestCase):

    def test_search_matches_by_capability(self):
        skills_registry.register(_skill(name="research", capabilities=["research", "web search"]))
        skills_registry.register(_skill(name="cooking", description="Suggest recipes.", capabilities=["recipes"]))
        results = skills_registry.search("please research this topic")
        self.assertEqual([s.name for s in results], ["research"])

    def test_search_excludes_disabled_skills(self):
        skills_registry.register(_skill(name="research", capabilities=["research"], enabled=False))
        self.assertEqual(skills_registry.search("research this"), [])

    def test_search_no_match_returns_empty(self):
        skills_registry.register(_skill())
        self.assertEqual(skills_registry.search("completely unrelated words here"), [])

    def test_search_respects_limit(self):
        for i in range(5):
            skills_registry.register(_skill(name=f"skill{i}", capabilities=["testing"]))
        results = skills_registry.search("testing", limit=2)
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
