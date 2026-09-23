"""Build provider prompts from frozen eval case snapshots."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import llm_provider as provider  # noqa: E402

PROMPT_POLICY_VERSION = "2026-09-23-v1"


def build_prompts(case: dict, profile: dict) -> dict:
    """Build base and provider-specific prompts plus the request body shape sent live."""
    section = case["section"]
    n = int(case.get("item_target") or 8)
    language = case.get("language") or "English"
    policies = case.get("source_preferences") or {}
    candidates = list(case["candidates"])

    base_system = provider.SYSTEM_PROMPT.replace("{n}", str(n)).replace("{language}", language)
    if policies:
        base_system += "\nPRIORITY SOURCES: " + "; ".join(
            f"Prefer {name}; select at least {policy.get('min_new_items', 0)} distinct new stories when available. "
            "Prefer direct original reporting over aggregated copies."
            for name, policy in policies.items()
        )

    sent_system = provider.instructions_for_provider(profile["provider"], base_system)
    interests = (case.get("interests_excerpt") or "").strip()
    extra = (case.get("extra_context") or "").strip()
    user = (
        f"SECTION: {section}\n\nREADER INTERESTS:\n{interests}\n\n"
        + (f"CONTEXT:\n{extra}\n\n" if extra else "")
        + f"CANDIDATES ({len(candidates)}; fields: id, t=title, src=publisher, at=published MM-DDTHH:MM, new=1 if not previously published here, s=summary):\n"
        + json.dumps(provider.candidates_for_prompt(candidates), ensure_ascii=False, separators=(",", ":"))
    )

    model = profile.get("model") or ""
    reasoning = profile.get("reasoning_effort")
    if profile["provider"] == "openai":
        original = provider.OPENAI_REASONING_EFFORT
        if reasoning == "low":
            provider.OPENAI_REASONING_EFFORT = "low"
        try:
            _, request_body = provider.openai_request("REDACTED", model, sent_system, user)
        finally:
            provider.OPENAI_REASONING_EFFORT = original
    else:
        _, request_body = provider.anthropic_request("REDACTED", model, sent_system, user)

    return {
        "system_prompt_base": base_system,
        "system_prompt_sent": sent_system,
        "user_prompt": user,
        "candidate_count": len(candidates),
        "request_body": request_body,
    }


def replay_bundle(case: dict, *, profile: dict, prompts: dict, result: dict) -> dict:
    """Serializable record for offline replay and reporting."""
    return {
        "case_id": case["id"],
        "section": case["section"],
        "tags": case.get("tags", []),
        "profile_id": profile["id"],
        "provider": profile["provider"],
        "model": profile.get("model") or result.get("model"),
        "reasoning_effort": profile.get("reasoning_effort"),
        "prompt_policy_version": PROMPT_POLICY_VERSION,
        "system_prompt_base": prompts["system_prompt_base"],
        "system_prompt_sent": prompts["system_prompt_sent"],
        "user_prompt": prompts["user_prompt"],
        "request_body": prompts["request_body"],
        "candidate_count": prompts["candidate_count"],
        "candidates": case["candidates"],
        "source_preferences": case.get("source_preferences") or {},
        "item_target": case.get("item_target"),
        "result": result,
    }
