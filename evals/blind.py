"""Blinded side-by-side review sheet for paired provider outputs.

For each case run by both profiles, the two outputs are shown as "A" and "B"
in a random order. The mapping goes to a separate key file so a reviewer can
score before learning which provider wrote which output.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

BASELINE = "haiku"
CANDIDATES = ("luna-live", "luna-none")


def _render(result: dict, candidates: list[dict]) -> list[str]:
    if result.get("status") != "success" or not isinstance(result.get("content"), dict):
        return [f"_No output: {result.get('error') or result.get('status')}_"]
    content = result["content"]
    lines = ["Briefing:"]
    for bullet in content.get("briefing") or []:
        ids = ", ".join(str(i) for i in bullet.get("source_ids") or [])
        lines.append(f"- {bullet.get('text')} [{ids}]")
    lines.append("")
    lines.append("Selected items:")
    for item in content.get("items") or []:
        idx = item.get("id")
        source = candidates[idx]["source"] if isinstance(idx, int) and 0 <= idx < len(candidates) else "?"
        lines.append(f"- [{idx}] {item.get('title')} ({source}): {item.get('summary')}")
    return lines


def write_blind_review(run_dir: Path, cases: dict[str, dict], rng=None) -> Path | None:
    rng = rng or secrets.SystemRandom()
    bundles = {}
    for path in sorted(run_dir.glob("*__*.json")):
        case_id, profile_id = path.stem.split("__", 1)
        bundles.setdefault(case_id, {})[profile_id] = json.loads(path.read_text())
    def candidate(cid):
        return next((p for p in CANDIDATES if p in bundles[cid]), None)

    pairs = [cid for cid in sorted(bundles) if BASELINE in bundles[cid] and candidate(cid)]
    if not pairs:
        return None
    key = {}
    lines = [
        "# Blind review",
        "",
        "Score each case before opening `blind-key.json`. For each case note which",
        "output (A or B) is better, or `tie`, and flag any claim not supported by",
        "the candidate titles/summaries. Bracketed numbers are candidate IDs.",
        "",
    ]
    for case_id in pairs:
        case = cases[case_id]
        order = [BASELINE, candidate(case_id)]
        rng.shuffle(order)
        key[case_id] = {"A": order[0], "B": order[1]}
        lines += [f"## {case_id} — {case['section']}", "", "<details><summary>Candidates</summary>", ""]
        for idx, cand in enumerate(case["candidates"]):
            lines.append(f"{idx}. {cand['title']} ({cand['source']})")
        lines += ["", "</details>", ""]
        for label, profile_id in zip("AB", order):
            lines += [f"### Output {label}", ""]
            lines += _render(bundles[case_id][profile_id]["result"], case["candidates"])
            lines.append("")
        lines += ["Verdict (A / B / tie): ____   Unsupported claims: ____", ""]
    (run_dir / "blind-key.json").write_text(json.dumps(key, indent=2) + "\n")
    out = run_dir / "blind-review.md"
    out.write_text("\n".join(lines))
    return out
