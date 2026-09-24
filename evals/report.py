"""Render markdown reports from eval run directories."""
from __future__ import annotations

import json
from pathlib import Path


def write_report(run_dir: Path) -> Path:
    summary = json.loads((run_dir / "summary.json").read_text())
    status = summary.get("status", "unknown")
    lines = [
        "# T08 evaluation run",
        "",
        f"- Started: {summary.get('started_at')}",
        f"- Live provider calls: {'yes' if summary.get('live') else 'no'}",
        f"- Run status: **{status}**",
    ]
    if summary.get("incomplete_reason"):
        lines.append(f"- Incomplete reason: {summary['incomplete_reason']}")
    if summary.get("missing_credentials"):
        lines.append(f"- Missing credentials: {', '.join(summary['missing_credentials'])}")

    lines.extend(
        [
            f"- Prompt policy: {summary.get('prompt_policy_version')}",
            f"- Planned calls: {summary.get('planned_calls', '—')}",
            f"- Completed calls: {summary.get('completed_calls', '—')}",
            f"- Successful calls: {summary.get('successful_calls', '—')}",
            f"- Failed calls: {summary.get('failed_calls', '—')}",
            f"- Calls failing mechanical checks: {summary.get('checks_failed', '—')}",
            "",
            "## Cases",
            "",
            "| Case | Profile | Status | Checks | Latency ms | Input tok | Output tok | Attempts | Recorded USD |",
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary.get("cases", []):
        usage = row.get("usage") or {}
        lines.append(
            f"| {row['case_id']} | {row['profile_id']} | {row.get('status')} | "
            f"{'pass' if row.get('passed_checks') else 'fail'} | {row.get('latency_ms') or '—'} | "
            f"{usage.get('input_tokens', '—')} | {usage.get('output_tokens', '—')} | "
            f"{usage.get('attempts', '—')} | {row.get('recorded_cost_usd', '—')} |"
        )

    spend = summary.get("spend_usd") or {}
    if spend:
        lines.extend(
            [
                "",
                "## Spend",
                "",
                f"- Recorded total USD: {spend.get('recorded', '—')}",
                f"- Pre-run estimate USD: {spend.get('estimated_pre_run', '—')}",
                f"- Budget cap USD: {spend.get('budget_cap', '—')}",
            ]
        )

    usage_totals = summary.get("usage_totals") or {}
    if usage_totals:
        lines.extend(
            [
                "",
                "## Usage totals",
                "",
                f"- Input tokens: {usage_totals.get('input_tokens', 0)}",
                f"- Output tokens: {usage_totals.get('output_tokens', 0)}",
                f"- Reasoning tokens: {usage_totals.get('reasoning_tokens', 0)}",
                f"- Retry attempts: {usage_totals.get('attempts', 0)}",
                f"- Total latency ms: {usage_totals.get('latency_ms', 0)}",
            ]
        )

    if summary.get("estimate"):
        est = summary["estimate"]
        lines.extend(
            [
                "",
                "## Pre-run estimate",
                "",
                f"- Provider calls: {est['provider_calls']}",
                f"- Estimated total USD: {est['estimated_usd']['total']}",
                f"- Note: {est['note']}",
            ]
        )

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Mechanical checks do not prove factual accuracy.",
            "- Human or Sol review is required for unsupported claims and editorial quality.",
            "- A status of `incomplete` or `failed` means this run must not approve a model switch.",
            "- Live quality remains unverified when `--live` was not authorized, credentials were missing, or any planned call failed.",
            "",
        ]
    )

    out = run_dir / "report.md"
    out.write_text("\n".join(lines))
    return out
