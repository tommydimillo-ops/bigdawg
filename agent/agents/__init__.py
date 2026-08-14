"""Importing this package registers the four built-in coworker agents as
a side effect -- mirrors tools/schemas/__init__.py populating tools.
registry, and agent/brain.py's load_all_skills() populating agent.
skills.registry, both on first import. Anything that needs the Agent
Manager to actually have agents available (agent/executor.py, tests,
the dashboard) gets this for free just by importing anything under
agent.agents; Python's own module caching means this only ever runs
once per process.
"""
from agent.agents import manager
from agent.agents.coding import CodingAgent
from agent.agents.memory import MemoryAgent
from agent.agents.qa import QAAgent
from agent.agents.research import ResearchAgent

for _agent_cls in (CodingAgent, ResearchAgent, QAAgent, MemoryAgent):
    _instance = _agent_cls()
    if _instance.metadata.name not in manager._REGISTRY:
        manager.register(_instance)
