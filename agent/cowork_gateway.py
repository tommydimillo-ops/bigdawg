"""Integration boundary for Claude Cowork -- currently a status-only
stub, deliberately.

There is no documented, programmatic Cowork API available to this
project to integrate against. Rather than inventing an undocumented
protocol or faking a working integration, this module only ever reports
its own status honestly (mirroring agent/provider_health.py's existing
"configured vs initialized" vocabulary, not a new one) and gives
agent.delegation.decide() a clean place to check before ever considering
DelegationDestination.COWORK as a real option. When a supported,
documented interface exists, this is where it gets wired in -- through
it, not around it -- without needing another rewrite of the delegation
policy or executor.py.

Jarvis remains the orchestrator regardless: per this project's Phase 6.5
security requirements, any future Cowork integration must still flow
every resulting action through the existing tools.registry/agent.autonomy/
agent.executor path -- this stub takes no position on that beyond noting
it, since there is nothing to actually execute here yet.
"""
from enum import Enum


class CoworkStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


def status() -> CoworkStatus:
    """Always UNAVAILABLE today -- there is no supported, documented
    Cowork API this project can call. Not NOT_CONFIGURED: that would
    imply configuration (an API key, a setting) could make it available,
    which isn't true yet either."""
    return CoworkStatus.UNAVAILABLE


def is_available() -> bool:
    return status() == CoworkStatus.AVAILABLE


def describe_status() -> str:
    return (
        "Cowork integration is unavailable -- no documented, programmatic "
        "Cowork API exists for this project to call yet. This is a known, "
        "reported state, not an error."
    )
