"""Snapshot today's real candidate lists as eval cases.

Runs the production ingestion and selection (no provider calls, no writes to
data/) and saves one case per AI section, so both providers are compared on
exactly the candidates a real edition would send them.

    python evals/snapshot_live.py --output /tmp/live-cases
    python evals/run.py --live --cases-dir /tmp/live-cases --profile haiku --profile luna-none
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402

import build  # noqa: E402
from ingestion import Fetcher, MemoryFeedCache  # noqa: E402
from selection import select_candidates  # noqa: E402

CANDIDATE_FIELDS = ("title", "source", "feed_name", "published", "new", "summary", "url", "key")


def snapshot_cases(cfg: dict, pools: dict, prices: list[dict], stamp: str) -> list[dict]:
    price_ctx = "Today's price moves: " + "; ".join(
        f"{p['label']} {p['change_pct']:+.2f}%" for p in prices if p.get("change_pct") is not None
    )
    cases = []
    for section in build.AI_SECTIONS:
        selected = select_candidates(pools[section], cfg, section) if pools[section] else []
        if not selected:
            continue
        case = {
            "id": f"live-{section}",
            "section": section,
            "tags": ["live", stamp],
            "language": cfg.get("language", "English"),
            "item_target": cfg["item_targets"].get(section, 8),
            "interests_excerpt": cfg["interests"].strip(),
            "source_preferences": cfg.get("source_preferences", {}).get(section, {}),
            "candidates": [{key: it.get(key) for key in CANDIDATE_FIELDS} for it in selected],
        }
        if section == "finance":
            case["extra_context"] = price_ctx
        cases.append(case)
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Snapshot live candidate lists as eval cases")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, help="offline feed fixtures (tests/make_fixtures.py)")
    parser.add_argument("--now", help="override current time (ISO), for fixture runs")
    args = parser.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    fetcher = Fetcher(args.fixtures, MemoryFeedCache())
    seen = build.load_seen()
    fin_items, prices = build.build_finance(fetcher, cfg, seen, now)
    pools = {
        "news": build.build_news(fetcher, cfg, seen, now),
        "sport": build.build_sport(fetcher, cfg, seen, now),
        "finance": fin_items,
    }
    cases = snapshot_cases(cfg, pools, prices, now.strftime("%Y-%m-%dT%H:%MZ"))
    args.output.mkdir(parents=True, exist_ok=True)
    for case in cases:
        (args.output / f"{case['id']}.json").write_text(json.dumps(case, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {len(cases)} live cases to {args.output}: " + ", ".join(
        f"{c['section']}={len(c['candidates'])}" for c in cases))
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
