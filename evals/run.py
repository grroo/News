"""Run T08 provider evaluations (offline by default)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import llm_provider as provider  # noqa: E402
from evals.checks import run_all_checks  # noqa: E402
from evals.prompts import PROMPT_POLICY_VERSION, build_prompts, replay_bundle  # noqa: E402

EVALS = Path(__file__).resolve().parent
MANIFEST_PATH = EVALS / "manifest.json"
CASES_DIR = EVALS / "cases"
FIXTURES_DIR = EVALS / "fixtures"
DEFAULT_RUNS = EVALS / "runs"

# Heuristic token averages from measured September 2026 workload (README).
AVG_INPUT_TOKENS = 4600
AVG_OUTPUT_TOKENS = 830
HAIKU_RATES = (1.0, 5.0)
LUNA_RATES = (0.10, 0.50)


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def load_case(case_id: str) -> dict:
    path = CASES_DIR / f"{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(case_id)
    return json.loads(path.read_text())


def profile_applies(profile: dict, case_id: str) -> bool:
    allowed = profile.get("case_ids")
    if allowed:
        return case_id in allowed
    return True


def estimate_cost(profiles: list[dict], case_ids: list[str]) -> dict:
    calls = sum(1 for case_id in case_ids for profile in profiles if profile_applies(profile, case_id))
    haiku = sum(1 for p in profiles if p["provider"] == "anthropic" for cid in case_ids if profile_applies(p, cid))
    luna = calls - haiku

    def cost(count, rates):
        inp, out = rates
        return round((count * AVG_INPUT_TOKENS * inp / 1_000_000) + (count * AVG_OUTPUT_TOKENS * out / 1_000_000), 4)

    return {
        "provider_calls": calls,
        "haiku_calls": haiku,
        "luna_calls": luna,
        "estimated_usd": {
            "haiku": cost(haiku, HAIKU_RATES),
            "luna": cost(luna, LUNA_RATES),
            "total": round(cost(haiku, HAIKU_RATES) + cost(luna, LUNA_RATES), 4),
        },
        "note": "Heuristic only; retries, reasoning and judging are excluded.",
    }


def call_profile(profile: dict, system: str, user: str, candidate_count: int, *, live: bool) -> dict:
    api_key = os.environ.get(provider.credential_env_var(profile["provider"]))
    if not live:
        return provider.generate_briefing(
            provider=profile["provider"],
            model=profile.get("model") or "",
            api_key=None,
            system=system,
            user=user,
            candidate_count=candidate_count,
        )
    if profile.get("reasoning_effort") == "low" and profile["provider"] == "openai":
        original = provider.OPENAI_REASONING_EFFORT
        provider.OPENAI_REASONING_EFFORT = "low"
        try:
            return provider.generate_briefing(
                provider=profile["provider"],
                model=profile.get("model") or "",
                api_key=api_key,
                system=system,
                user=user,
                candidate_count=candidate_count,
            )
        finally:
            provider.OPENAI_REASONING_EFFORT = original
    return provider.generate_briefing(
        provider=profile["provider"],
        model=profile.get("model") or "",
        api_key=api_key,
        system=system,
        user=user,
        candidate_count=candidate_count,
    )


def run_case(case: dict, profile: dict, *, live: bool, fixture: dict | None = None) -> dict:
    system, user, candidate_count = build_prompts(case)
    if fixture is not None:
        result = fixture
    else:
        result = call_profile(profile, system, user, candidate_count, live=live)
    checks = run_all_checks(
        result,
        candidates=case["candidates"],
        source_preferences=case.get("source_preferences") or {},
        item_target=case.get("item_target"),
        sparse=bool(case.get("sparse")),
        tags=case.get("tags") or [],
    )
    bundle = replay_bundle(case, profile=profile, system=system, user=user, result=result)
    bundle["checks"] = checks
    return bundle


def validate_cases(case_ids: list[str]) -> list[str]:
    issues = []
    for case_id in case_ids:
        case = load_case(case_id)
        for key in ("id", "section", "language", "candidates"):
            if key not in case:
                issues.append(f"{case_id}: missing {key}")
        if case.get("id") != case_id:
            issues.append(f"{case_id}: id mismatch")
        if not case.get("candidates"):
            issues.append(f"{case_id}: empty candidates")
    return issues


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T08 provider evaluation harness")
    parser.add_argument("--validate-cases", action="store_true", help="Validate public case snapshots")
    parser.add_argument("--estimate", action="store_true", help="Print live cost estimate")
    parser.add_argument("--check-fixtures", action="store_true", help="Run checks on bundled fixture outputs")
    parser.add_argument("--live", action="store_true", help="Call providers (requires keys and EVAL_LIVE=1)")
    parser.add_argument("--case", action="append", dest="cases", help="Case id (repeatable)")
    parser.add_argument("--profile", action="append", dest="profiles", help="Profile id (repeatable)")
    parser.add_argument("--output", type=Path, default=DEFAULT_RUNS, help="Output directory for replay bundles")
    args = parser.parse_args(argv)

    manifest = load_manifest()
    case_ids = args.cases or manifest["case_ids"]
    profiles = [p for p in manifest["profiles"] if not args.profiles or p["id"] in args.profiles]

    if args.validate_cases:
        issues = validate_cases(case_ids)
        if issues:
            print("\n".join(issues), file=sys.stderr)
            return 1
        print(f"Validated {len(case_ids)} cases.")
        return 0

    if args.estimate:
        print(json.dumps(estimate_cost(profiles, case_ids), indent=2))
        return 0

    if args.check_fixtures:
        failures = 0
        for path in sorted(FIXTURES_DIR.glob("*.json")):
            payload = json.loads(path.read_text())
            case = load_case(payload["case_id"])
            profile = next(p for p in manifest["profiles"] if p["id"] == payload["profile_id"])
            bundle = run_case(case, profile, live=False, fixture=payload["result"])
            expect_pass = payload.get("expect_pass", True)
            ok = bundle["checks"]["passed"] if expect_pass else not bundle["checks"]["passed"]
            print(f"{path.name}: {'PASS' if ok else 'FAIL'}")
            if not ok:
                failures += 1
                for issue in bundle["checks"].get("issues", []):
                    print(f"  - {issue}")
        return 1 if failures else 0

    if args.live:
        if os.environ.get("EVAL_LIVE") != "1":
            print("Set EVAL_LIVE=1 to authorize paid provider calls.", file=sys.stderr)
            return 2
        estimate = estimate_cost(profiles, case_ids)
        budget = float(manifest.get("live_budget_usd") or 0.75)
        if estimate["estimated_usd"]["total"] > budget:
            print(f"Estimated ${estimate['estimated_usd']['total']} exceeds budget ${budget}.", file=sys.stderr)
            return 2

    run_dir = args.output / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "live": bool(args.live),
        "prompt_policy_version": PROMPT_POLICY_VERSION,
        "cases": [],
        "estimate": estimate_cost(profiles, case_ids) if args.live else None,
    }

    for case_id in case_ids:
        case = load_case(case_id)
        for profile in profiles:
            if not profile_applies(profile, case_id):
                continue
            bundle = run_case(case, profile, live=args.live)
            out = run_dir / f"{case_id}__{profile['id']}.json"
            out.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
            summary["cases"].append(
                {
                    "case_id": case_id,
                    "profile_id": profile["id"],
                    "status": bundle["result"].get("status"),
                    "passed_checks": bundle["checks"]["passed"],
                    "latency_ms": bundle["result"].get("latency_ms"),
                    "usage": bundle["result"].get("usage"),
                }
            )

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    from evals.report import write_report  # noqa: WPS433

    write_report(run_dir)
    print(f"Wrote run to {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
