"""Tests for agent/skills/router.py -- deterministic skill matching, no
model call. candidates is injected directly in these tests rather than
going through the real registry, so they're independent of whatever
skills happen to be loaded from disk.

Run with: python -m unittest tests.test_skills_router -v
"""
import unittest

from agent.skills.models import Skill
from agent.skills.router import route


def _skill(name, **overrides):
    defaults = dict(
        name=name,
        description="A skill for router tests.",
        version="1.0",
        instructions="Do the thing.",
        capabilities=[],
    )
    defaults.update(overrides)
    return Skill(**defaults)


RESEARCH = _skill("research", description="Research a topic across sources.", capabilities=["research", "web search"])
DATA_ANALYSIS = _skill("data_analysis", description="Analyze a spreadsheet for trends.", capabilities=["data analysis", "spreadsheet"])
CANDIDATES = [RESEARCH, DATA_ANALYSIS]


class TestRoute(unittest.TestCase):

    def test_matches_the_right_skill(self):
        rec = route("Can you research the best laptops under $1000?", candidates=CANDIDATES)
        self.assertTrue(rec.matched)
        self.assertEqual(rec.skill.name, "research")

    def test_matches_a_different_skill_for_a_different_request(self):
        rec = route("Analyze this spreadsheet and explain the trends.", candidates=CANDIDATES)
        self.assertTrue(rec.matched)
        self.assertEqual(rec.skill.name, "data_analysis")

    def test_unrelated_request_does_not_match(self):
        rec = route("Open Safari.", candidates=CANDIDATES)
        self.assertFalse(rec.matched)
        self.assertIsNone(rec.skill)

    def test_empty_request_does_not_match(self):
        rec = route("", candidates=CANDIDATES)
        self.assertFalse(rec.matched)

    def test_no_candidates_does_not_match(self):
        rec = route("research this topic", candidates=[])
        self.assertFalse(rec.matched)
        self.assertIn("no skills registered", rec.reason)

    def test_default_candidates_come_from_the_registry(self):
        # candidates=None (the real default) falls through to
        # registry.list_skills(enabled_only=True) -- covered directly
        # against an isolated registry in test_skills_registry.py's
        # TestSearch; this just confirms route() actually calls it rather
        # than silently requiring candidates to always be passed in.
        import agent.skills.registry as skills_registry
        real_registry = dict(skills_registry._REGISTRY)
        skills_registry.clear()
        try:
            skills_registry.register(RESEARCH)
            rec = route("research the best laptops")
            self.assertTrue(rec.matched)
            self.assertEqual(rec.skill.name, "research")
        finally:
            skills_registry.clear()
            skills_registry._REGISTRY.update(real_registry)

    def test_confidence_is_between_zero_and_one(self):
        rec = route("Can you research the best laptops under $1000?", candidates=CANDIDATES)
        self.assertGreaterEqual(rec.confidence, 0.0)
        self.assertLessEqual(rec.confidence, 1.0)

    def test_single_incidental_word_overlap_does_not_match(self):
        # One shared, generic word (no capability overlap) should score
        # below MIN_CONFIDENCE_SCORE and not be treated as a real match.
        weak_skill = _skill("weak", description="Track upcoming birthday reminders.", capabilities=[])
        rec = route("What's a good gift idea?", candidates=[weak_skill])
        self.assertFalse(rec.matched)


if __name__ == "__main__":
    unittest.main()
