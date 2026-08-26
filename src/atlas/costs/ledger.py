"""Cost ledger + budget rail.

Operating System §3: "A technical failure never becomes a zero score."
System Zero acceptance (Execution Plan §3): "Every API/tool call creates a
cost record; unknown cost is flagged, never treated as zero." Those two
rules are why `is_unknown_cost` exists on every row here instead of a
silent `0.0` default — a cost we failed to compute is a defect to
investigate, not a free call.

Budget rail (Operating System §10): checked before each batch. Alert at
80% of the per-client monthly budget, stop non-critical work at 100%.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.config import PROVIDER_PRICING
from atlas.db.client import get_db


@dataclass
class CostRecord:
    observation_id: str
    property_id: str
    provider: str
    input_tokens: int
    output_tokens: int
    search_units: int
    total_cost_usd: float
    is_unknown_cost: bool


def compute_cost(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    search_units: int,
) -> tuple[float, bool]:
    """Returns (total_cost_usd, is_unknown_cost)."""
    price = PROVIDER_PRICING.get(provider)
    if price is None or price.input_per_mtok == 0.0 and price.output_per_mtok == 0.0:
        # Pricing not yet confirmed for this provider (see config.py TODOs) —
        # flag rather than silently charge nothing.
        return 0.0, True

    token_cost = (input_tokens / 1_000_000) * price.input_per_mtok
    token_cost += (output_tokens / 1_000_000) * price.output_per_mtok
    search_cost = (search_units * price.search_unit_cost) if price.search_unit_cost else 0.0
    return round(token_cost + search_cost, 6), False


def record_cost(record: CostRecord) -> None:
    db = get_db()
    db.table("costs").insert(
        {
            "observation_id": record.observation_id,
            "property_id": record.property_id,
            "provider": record.provider,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "search_units": record.search_units,
            "total_cost_usd": record.total_cost_usd,
            "is_unknown_cost": record.is_unknown_cost,
        }
    ).execute()


def check_budget_rail(property_id: str, monthly_budget_usd: float) -> str:
    """Returns 'ok' | 'alert' | 'stop'.

    'alert' at 80% of monthly_budget_usd spent this month, 'stop' at 100% —
    at which point only critical work (in-flight replicates needed for a
    baseline/validation pair) should proceed; non-critical work (Sentinel,
    Discovery) should be deferred to next cycle.
    """
    db = get_db()
    # TODO(Week 1): scope this query to the current calendar month.
    result = db.table("costs").select("total_cost_usd").eq("property_id", property_id).execute()
    spent = sum(row["total_cost_usd"] for row in result.data)

    if monthly_budget_usd <= 0:
        return "ok"
    ratio = spent / monthly_budget_usd
    if ratio >= 1.0:
        return "stop"
    if ratio >= 0.8:
        return "alert"
    return "ok"
