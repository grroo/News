"""Versioned provider pricing for the public edition ledger.

Prices are estimates. A timeout with no reported tokens stays null rather than
zero. Reasoning tokens are not added to output tokens. Unknown models stay
unpriced instead of inheriting another model's rate.
"""
from __future__ import annotations

PRICE_TABLE_VERSION = "2026-09-23"

# USD per million tokens. Cached input is the published cache-read rate.
# Cache writes are 1.25x the uncached input rate. Output already includes
# reasoning when the provider reports both.
RATES = {
    "claude-haiku-4-5": {"input": 1.0, "cached_input": 0.1, "cache_write": 1.25, "output": 5.0},
    "claude-sonnet-4-6": {"input": 3.0, "cached_input": 0.3, "cache_write": 3.75, "output": 15.0},
    "claude-sonnet-4-5": {"input": 3.0, "cached_input": 0.3, "cache_write": 3.75, "output": 15.0},
    "claude-opus-4-1": {"input": 15.0, "cached_input": 1.5, "cache_write": 18.75, "output": 75.0},
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50},
}

_BUCKETS = (
    ("input_tokens", "input"),
    ("cached_input_tokens", "cached_input"),
    ("cache_write_tokens", "cache_write"),
    ("output_tokens", "output"),
)


def _number(value):
    if type(value) is bool or not isinstance(value, (int, float)):
        return None
    return value


def section_usage(section: str, result: dict | None) -> dict:
    usage = (result or {}).get("usage") or {}
    inputs = usage.get("pricing_inputs") if isinstance(usage.get("pricing_inputs"), dict) else {}
    row = {
        "section": section,
        "provider": (result or {}).get("provider"),
        "model": (result or {}).get("model"),
        "attempts": usage.get("attempts", 0) if result else 0,
        "unknown": bool(usage.get("unknown")),
        "provider_request_ids": list((result or {}).get("provider_request_ids") or []),
    }
    for key, _rate in _BUCKETS:
        value = _number(inputs.get(key))
        if value is None:
            value = _number(usage.get(key))
        if value is not None:
            row[key] = value
    reasoning = _number(inputs.get("reasoning_tokens"))
    if reasoning is None:
        reasoning = _number(usage.get("reasoning_tokens"))
    if reasoning is not None:
        row["reasoning_tokens"] = reasoning
    return row


def _cost(model: str, row: dict) -> tuple[float | None, bool]:
    rates = RATES.get(model or "")
    unknown = bool(row.get("unknown"))
    if rates is None:
        return None, True
    total = 0.0
    saw_tokens = False
    for key, rate_key in _BUCKETS:
        if key not in row:
            continue
        saw_tokens = True
        total += row[key] * rates[rate_key] / 1_000_000
    if unknown and not saw_tokens:
        return None, True
    return round(total, 6), unknown


def ledger(rows: list[dict], *, editions_per_day: int = 3) -> dict:
    attempts = 0
    unknown = False
    unreported = False
    combined = {key: 0 for key, _rate in _BUCKETS}
    reasoning = 0
    saw_reasoning = False
    cost = 0.0
    priced = False
    for row in rows:
        attempts += int(row.get("attempts") or 0)
        row_cost, row_unreported = _cost(row.get("model") or "", row)
        if row.get("unknown") or row_unreported:
            unknown = unknown or bool(row.get("unknown"))
            unreported = True
        if row_cost is not None:
            cost += row_cost
            priced = True
        for key, _rate in _BUCKETS:
            if key in row:
                combined[key] += row[key]
        if "reasoning_tokens" in row:
            reasoning += row["reasoning_tokens"]
            saw_reasoning = True
    if not priced or (unreported and cost == 0):
        est = None
    else:
        est = round(cost, 6)
    forecast = None if est is None else round(est * editions_per_day * 30, 2)
    summary = {
        "price_table_version": PRICE_TABLE_VERSION,
        "calls": attempts,
        "attempts": attempts,
        "unknown": unknown,
        "unreported_cost": unreported,
        "est_cost_usd": est,
        "est_month_usd": forecast,
        "forecast_month_usd": forecast,
        "forecast_basis": f"{editions_per_day} editions/day x 30, labelled forecast",
        "sections": rows,
    }
    for key, _rate in _BUCKETS:
        if any(key in row for row in rows):
            summary[key] = combined[key]
    if saw_reasoning:
        summary["reasoning_tokens"] = reasoning
    return summary
