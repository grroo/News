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
from usage_ledger import RATES, ledger, section_usage  # noqa: E402
from evals.checks import run_all_checks  # noqa: E402
from evals.prompts import PROMPT_POLICY_VERSION, build_prompts, replay_bundle  # noqa: E402

EVALS = Path(__file__).resolve().parent
MANIFEST_PATH = EVALS / "manifest.json"
CASES_DIR = EVALS / "cases"
FIXTURES_DIR = EVALS / "fixtures"
DEFAULT_RUNS = EVALS / "runs"

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


def planned_calls(profiles: list[dict], case_ids: list[str]) -> list[tuple[str, dict]]:
    return [
        (case_id, profile)
        for case_id in case_ids
        for profile in profiles
        if profile_applies(profile, case_id)
    ]


def required_credentials(profiles: list[dict]) -> list[str]:
    missing = []
    seen = set()
    for profile in profiles:
        var = provider.credential_env_var(profile["provider"])
        if var not in seen:
            seen.add(var)
            if not os.environ.get(var):
                missing.append(var)
    return missing


def estimate_cost(profiles: list[dict], case_ids: list[str]) -> dict:
    calls = len(planned_calls(profiles, case_ids))
    haiku = sum(1 for _, p in planned_calls(profiles, case_ids) if p["provider"] == "anthropic")
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


def price_result(result: dict, profile: dict) -> float | None:
    """Price every reported token bucket with the production price table.

    Returns None when any attempt's charge is unknown (for example a timeout
    with no usage). Callers must treat None as a stop condition, not as zero.
    """
    model = result.get("model") or profile.get("model") or ""
    summary = ledger([section_usage(profile["id"], {**result, "model": model})])
    if summary["est_cost_usd"] is None or summary["unreported_cost"]:
        return None
    return summary["est_cost_usd"]


def worst_case_cost(profile: dict, prompts: dict) -> float:
    """Upper bound for one call, reserved before it starts.

    Assumes every allowed attempt is billed, with input at two characters per
    token (conservative for English/JSON) and the full output allowance.
    """
    rates = RATES[profile.get("model") or ""]
    input_tokens = (len(prompts["system_prompt_sent"]) + len(prompts["user_prompt"])) // 2 + 1
    output_tokens = (
        provider.openai_max_output_tokens(profile.get("reasoning_effort") or "none")
        if profile["provider"] == "openai" else 2500
    )
    per_attempt = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    return round(per_attempt * provider.DEFAULT_MAX_ATTEMPTS, 6)


def accumulate_usage(totals: dict, result: dict) -> None:
    usage = result.get("usage") or {}
    totals["input_tokens"] = totals.get("input_tokens", 0) + int(usage.get("input_tokens") or 0)
    totals["output_tokens"] = totals.get("output_tokens", 0) + int(usage.get("output_tokens") or 0)
    totals["reasoning_tokens"] = totals.get("reasoning_tokens", 0) + int(usage.get("reasoning_tokens") or 0)
    totals["attempts"] = totals.get("attempts", 0) + int(usage.get("attempts") or 0)
    totals["latency_ms"] = totals.get("latency_ms", 0) + int(result.get("latency_ms") or 0)


def call_profile(profile: dict, prompts: dict, candidate_count: int, *, live: bool) -> dict:
    api_key = os.environ.get(provider.credential_env_var(profile["provider"])) if live else None
    return provider.generate_briefing(
        provider=profile["provider"],
        model=profile.get("model") or "",
        api_key=api_key,
        system=prompts["system_prompt_sent"],
        user=prompts["user_prompt"],
        candidate_count=candidate_count,
        reasoning_effort=profile.get("reasoning_effort") if profile["provider"] == "openai" else None,
    )


def run_case(case: dict, profile: dict, *, live: bool, fixture: dict | None = None) -> dict:
    prompts = build_prompts(case, profile)
    if fixture is not None:
        result = fixture
    else:
        result = call_profile(profile, prompts, prompts["candidate_count"], live=live)
    checks = run_all_checks(
        result,
        candidates=case["candidates"],
        source_preferences=case.get("source_preferences") or {},
        item_target=case.get("item_target"),
        sparse=bool(case.get("sparse")),
        tags=case.get("tags") or [],
    )
    bundle = replay_bundle(case, profile=profile, prompts=prompts, result=result)
    bundle["checks"] = checks
    bundle["recorded_cost_usd"] = price_result(result, profile)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T08 provider evaluation harness")
    parser.add_argument("--validate-cases", action="store_true", help="Validate public case snapshots")
    parser.add_argument("--estimate", action="store_true", help="Print live cost estimate")
    parser.add_argument("--check-fixtures", action="store_true", help="Run checks on bundled fixture outputs")
    parser.add_argument("--live", action="store_true", help="Call providers (requires keys and EVAL_LIVE=1)")
    parser.add_argument("--case", action="append", dest="cases", help="Case id (repeatable)")
    parser.add_argument("--profile", action="append", dest="profiles", help="Profile id (repeatable)")
    parser.add_argument("--output", type=Path, default=DEFAULT_RUNS, help="Output directory for replay bundles")
    parser.add_argument("--cases-dir", type=Path, help="Use cases from this directory (e.g. evals/snapshot_live.py output)")
    args = parser.parse_args(argv)

    manifest = load_manifest()
    global CASES_DIR
    CASES_DIR = args.cases_dir or EVALS / "cases"
    if args.cases_dir:
        case_ids = args.cases or sorted(path.stem for path in CASES_DIR.glob("*.json"))
    else:
        case_ids = args.cases or manifest["case_ids"]
    profiles = [p for p in manifest["profiles"] if not args.profiles or p["id"] in args.profiles]
    if not case_ids:
        print("No cases to run.", file=sys.stderr)
        return 2
    schedule = planned_calls(profiles, case_ids)

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

    if args.live and os.environ.get("EVAL_LIVE") != "1":
        print("Set EVAL_LIVE=1 to authorize paid provider calls.", file=sys.stderr)
        return 2

    missing = required_credentials(profiles) if args.live else []
    if args.live and missing:
        print(f"Missing credentials: {', '.join(missing)}", file=sys.stderr)
        return 2

    budget = float(manifest.get("live_budget_usd") or 0.75)
    pre_estimate = estimate_cost(profiles, case_ids)
    if args.live and pre_estimate["estimated_usd"]["total"] > budget:
        print(
            f"Estimated ${pre_estimate['estimated_usd']['total']} exceeds budget ${budget}.",
            file=sys.stderr,
        )
        return 2

    run_dir = args.output / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "live": bool(args.live),
        "status": "dry_run" if not args.live else "running",
        "prompt_policy_version": PROMPT_POLICY_VERSION,
        "planned_calls": len(schedule),
        "completed_calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "checks_failed": 0,
        "missing_credentials": missing,
        "cases": [],
        "estimate": pre_estimate if args.live else None,
        "usage_totals": {},
        "spend_usd": {
            "budget_cap": budget if args.live else None,
            "estimated_pre_run": pre_estimate["estimated_usd"]["total"] if args.live else None,
            "recorded": 0.0,
        },
    }

    recorded_spend = 0.0
    incomplete_reason = None

    for case_id, profile in schedule:
        case = load_case(case_id)
        if args.live and recorded_spend + worst_case_cost(profile, build_prompts(case, profile)) > budget:
            incomplete_reason = "budget_exhausted"
            break
        bundle = run_case(case, profile, live=args.live)
        out = run_dir / f"{case_id}__{profile['id']}.json"
        out.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")

        result = bundle["result"]
        success = result.get("status") == "success"
        cost = bundle.get("recorded_cost_usd")
        if cost is not None:
            recorded_spend += cost
        if not bundle["checks"]["passed"]:
            summary["checks_failed"] += 1

        summary["completed_calls"] += 1
        if success:
            summary["successful_calls"] += 1
        else:
            summary["failed_calls"] += 1
        accumulate_usage(summary["usage_totals"], result)
        summary["cases"].append(
            {
                "case_id": case_id,
                "profile_id": profile["id"],
                "status": result.get("status"),
                "passed_checks": bundle["checks"]["passed"],
                "check_issues": bundle["checks"].get("issues", []),
                "check_warnings": bundle["checks"].get("warnings", []),
                "latency_ms": result.get("latency_ms"),
                "usage": result.get("usage"),
                "recorded_cost_usd": cost,
            }
        )
        if args.live and cost is None:
            # Unknown spend could exceed the cap; stop rather than count it as zero.
            incomplete_reason = "unknown_cost"
            break

    summary["spend_usd"]["recorded"] = round(recorded_spend, 6)

    if args.live:
        if incomplete_reason:
            summary["status"] = "incomplete"
            summary["incomplete_reason"] = incomplete_reason
        elif summary["completed_calls"] < summary["planned_calls"]:
            summary["status"] = "incomplete"
            summary["incomplete_reason"] = "planned_calls_not_finished"
        elif summary["failed_calls"]:
            summary["status"] = "failed"
            summary["incomplete_reason"] = "provider_failures"
        elif summary["checks_failed"]:
            summary["status"] = "failed"
            summary["incomplete_reason"] = "mechanical_checks_failed"
        else:
            summary["status"] = "complete"
    else:
        summary["status"] = "dry_run"

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    from evals.report import write_report  # noqa: WPS433

    write_report(run_dir)
    if args.live:
        from evals.blind import write_blind_review  # noqa: WPS433

        write_blind_review(run_dir, {cid: load_case(cid) for cid, _profile in schedule})
    print(f"Wrote run to {run_dir} (status={summary['status']})")

    if args.live and summary["status"] != "complete":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
