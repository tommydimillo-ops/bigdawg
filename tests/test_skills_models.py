"""Tests for agent/skills/models.py -- the Skill dataclass and its
deterministic matching score (no model call).

Run with: python -m unittest tests.test_skills_models -v
"""
import unittest

from agent.skills.models import RiskLevel, Skill, SkillSource


def _skill(**overrides):
    defaults = dict(
        name="research",
        description="Research a topic across multiple sources.",
        version="1.0",
        instructions="Search multiple sources, cross-check facts.",
        capabilities=["research", "web search"],
    )
    defaults.update(overrides)
    return Skill(**defaults)


class TestSkillDefaults(unittest.TestCase):

    def test_defaults(self):
        skill = _skill()
        self.assertEqual(skill.risk_level, RiskLevel.LOW)
        self.assertTrue(skill.enabled)
        self.assertEqual(skill.source, SkillSource.LOCAL)
        self.assertEqual(skill.required_tools, [])
        self.assertEqual(skill.metadata, {})
        self.assertIsNone(skill.path)

    def test_is_frozen(self):
        skill = _skill()
        with self.assertRaises(Exception):
            skill.name = "changed"


class TestSkillMatching(unittest.TestCase):

    def test_matches_on_capability(self):
        skill = _skill()
        score = skill.matches(["research", "laptops"])
        self.assertGreater(score, 0)

    def test_matches_on_description_word(self):
        skill = _skill()
        score = skill.matches(["multiple", "sources"])
        self.assertGreater(score, 0)

    def test_no_match_scores_zero(self):
        skill = _skill()
        score = skill.matches(["weather", "forecast"])
        self.assertEqual(score, 0)

    def test_capability_hits_score_higher_than_description_hits(self):
        skill = _skill()
        capability_score = skill.matches(["research"])  # exact capability word
        description_score = skill.matches(["multiple"])  # only in description
        self.assertGreater(capability_score, description_score)

    def test_empty_query_terms_scores_zero(self):
        skill = _skill()
        self.assertEqual(skill.matches([]), 0)


if __name__ == "__main__":
    unittest.main()
