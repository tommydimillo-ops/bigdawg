"""Phase 9 Milestone 2's minimal budget-control foundation -- reuses
agent/usage.py's existing cost accounting (Phase 8) rather than building
a second, parallel spend-tracking store. No new persistent state: every
check here is a fresh sum over the same usage_history.json records
pages/1_Dashboard.py's own cost breakdowns already read.

Deliberately minimal, per Phase 9 Milestone 2's own explicit allowance
("if implementing full persistent monthly accounting is too large for
this milestone, implement the minimal clean foundation and explicitly
report what remains deferred" -- see CHANGELOG.md for what that leaves
out): a same-day spend ceiling, checked per-provider and globally,
against config/settings.py's daily_budget_usd/provider_daily_budget_usd.
No monthly rollups, no persisted warning-already-sent state, no
notification delivery -- those are real, separate features, not a
prerequisite for the router to be able to say "no" to an
over-budget candidate today.

A budget check failing must never make routing pick a MORE expensive
fallback -- that's why this is a filter (removes over-budget candidates
from consideration) applied before cost-based ranking, not a
after-the-fact warning a caller could ignore. See
agent/model_router.py's build_fallback_chain() for where this plugs in.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from agent.usage import cost_today, get_since
from config.settings import settings


@dataclass(frozen=True)
class BudgetStatus:
    provider: str
    spent_today_usd: float
    limit_usd: Optional[float]
    at_warning: bool
    over_limit: bool


def _midnight_today() -> float:
    return datetime.combine(datetime.now().date(), datetime.min.time()).timestamp()


def provider_spend_today(provider: str) -> float:
    """Sums today's estimated_cost_usd for one provider -- same
    get_since()-based approach as agent.usage.cost_today(), just filtered
    to a single provider instead of every record. Fails safe: 0.0 (not
    an exception, not None) if usage_history.json can't be read, since a
    budget check that can't determine spend should not block routing --
    an unreadable cost file is a usage.py problem, not a reason to also
    take routing down."""
    try:
        return sum(r.estimated_cost_usd for r in get_since(_midnight_today()) if r.provider == provider)
    except Exception:
        return 0.0


def provider_budget_status(provider: str) -> BudgetStatus:
    limit = settings.provider_daily_budget_usd
    spent = provider_spend_today(provider)
    at_warning = limit is not None and limit > 0 and (spent / limit) >= settings.budget_warning_threshold
    over_limit = limit is not None and spent >= limit
    return BudgetStatus(provider=provider, spent_today_usd=spent, limit_usd=limit, at_warning=at_warning, over_limit=over_limit)


def global_budget_status() -> BudgetStatus:
    limit = settings.daily_budget_usd
    spent = cost_today() or 0.0
    at_warning = limit is not None and limit > 0 and (spent / limit) >= settings.budget_warning_threshold
    over_limit = limit is not None and spent >= limit
    return BudgetStatus(provider="__global__", spent_today_usd=spent, limit_usd=limit, at_warning=at_warning, over_limit=over_limit)


def is_provider_within_budget(provider: str) -> bool:
    """The actual filter agent.model_router.build_fallback_chain() applies
    before ranking -- a provider that's already at its own or the global
    daily ceiling is removed from candidates entirely, not just flagged.
    Neither budget is configured by default (None disables the
    corresponding check), matching every other Phase 8 limit's
    convention -- so this is a no-op filter until the user opts in by
    setting a real number."""
    if global_budget_status().over_limit:
        return False
    if provider_budget_status(provider).over_limit:
        return False
    return True
