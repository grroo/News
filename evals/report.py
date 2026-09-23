"""Render markdown reports from eval run directories."""
from __future__ import annotations

import json
from pathlib import Path


def write_report(run_dir: Path) -> Path:
    summary = json.loads((run_dir / "summary.json").read_text())
    lines = [
        "# T08 evaluation run",
        "",
        f"- Started: {summary.get('started_at')}",
        f"- Live provider calls: {'yes' if summary.get('live') else 'no (dry run / missing keys)'}",
        f"- Prompt policy: {summary.get('prompt_policy_version')}",
        "",
        "## Cases",
        "",
        "| Case | Profile | Status | Checks | Latency ms | Input tok | Output tok |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in summary.get("cases", []):
        usage = row.get("usage") or {}
        lines.append(
            f"| {row['case_id']} | {row['profile_id']} | {row.get('status')} | "
            f"{'pass' if row.get('passed_checks') else 'fail'} | {row.get('latency_ms') or '—'} | "
            f"{usage.get('input_tokens', '—')} | {usage.get('output_tokens', '—')} |"
        )

    if summary.get("estimate"):
        est = summary["estimate"]
        lines.extend(
            [
                "",
                "## Cost estimate (pre-run)",
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
            "- Live quality remains unverified when `--live` was not authorized or keys were absent.",
            "",
        ]
    )

    out = run_dir / "report.md"
    out.write_text("\n".join(lines))
    return out
