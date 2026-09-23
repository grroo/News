# T02 — Add GPT-6 Luna behind a small provider adapter

Builder: Grok 4.7 medium; Luna medium is a suitable lower-cost alternative with the frozen contract. Reviewer: Sol high. Depends on T00. Own provider module/schema/tests; send config requests to T04 and secret/workflow requirements to T01.

Current code calls Anthropic Messages with x-api-key and a forced submit_briefing tool. A different model string alone will fail. Implement OpenAI Responses with `gpt-6-luna`, strict structured output and explicit reasoning effort `none` initially. Consult current official model/schema docs. Do not blindly copy Anthropic sampling or output parameters into OpenAI requests.

Keep the Anthropic implementation selectable for rollback. Normalize both providers to the same internal result. Separate request creation, response parsing and usage normalization enough to test them with fixtures; avoid adding a new orchestration framework. Production credentials must match the chosen provider and missing credentials must be an explicit failure.

Handle refusal, incomplete/truncated output, invalid JSON/schema, absent structured content, timeout, 429 and transient server failures. Bounded retries must respect the orchestrator's attempt allowance; malformed requests and authentication failures should not consume repeated attempts. Keep runtime source-ID validation, duplicate checks and preference enforcement after structured parsing. Candidate text is untrusted source material, not instructions.

Capture model/provider, input/cached/output tokens, reasoning details where reported, attempt counts, latency and provider request identifiers where safe. Do not double-count cached input or reasoning. A timed-out request may have incurred unreported cost: label it unknown instead of zero. Return pricing inputs to T04; do not duplicate price logic.

Acceptance: mocked success for both providers gives equivalent internal shape; invalid/out-of-set/bool IDs fail semantic checks; refusal/truncation/errors produce accurate outcomes; retry limits hold; no keys in logs or fixtures; rollback requires configuration only. Live quality comparison belongs to T08. Ask T01 to pass OPENAI_API_KEY as a secret without printing it. Document the exact selected model identifier and API parameters.

