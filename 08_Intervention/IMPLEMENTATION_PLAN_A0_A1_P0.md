# Implementation Plan — A0/A1 Separability, Observability, P0 Safety

**Status:** PROPOSAL — awaiting researcher approval. No existing code is modified
until approved.
**Date:** 2026-11-18
**Scope:** architecture separability/switchability, component observability,
P0 production safety, automated tests, documentation.
**Explicitly excluded:** formal human trials, freezing any PROVISIONAL scientific
value, selecting a model pair, changing the research question, editing
`runs/` or the synthetic pilot.

---

## 1. Definitions

- **A0** — the conventional sequential baseline. Exactly the current
  `BaselinePipeline` behaviour: full-utterance ASR → one LLM tool loop →
  deterministic tools → complete TTS → playback. **Frozen**; no behavioural
  edits except approved bug fixes.
- **A1** — the reliability/efficiency architecture. A new engine that composes
  the same providers, database, tools, prompts, and logging, and adds the five
  components below. Evaluated as a combined architecture.
- **P0** — production safety work on the deployed phone bridge
  (`src/nbva/phone.py`, `api/index.py`, `ui.html`). Production is not the
  research instrument; both use `BaselinePipeline` today, so P0 changes must be
  additive and must not alter A0 behaviour.

## 2. H3 — operationalisation and margin (margin NOT selected)

H3: a weak model + A1 approaches a strong model + A0 on task accuracy at lower
cost.

**Estimand.** Δ = p(M·A1) − p(S·A0), where p = proportion of trials with
`task_success = true`, scored by deterministic final-state assertions.

**Decision procedure (proposed, not frozen).** One-sided non-inferiority /
equivalence test on the risk difference with a pre-registered margin δ:

- H0: p(M·A1) − p(S·A0) ≤ −δ  (weak+A1 is worse by at least δ)
- H1: p(M·A1) − p(S·A0) > −δ   (non-inferior)

Report the point estimate and a 95% CI. "Approaches" is claimed only if the
lower CI bound exceeds −δ. A superiority claim requires the lower bound > 0.

**Candidate margins (choose ONE, or define another; none is selected here):**

| Option | Margin | Meaning |
|---|---|---|
| A | δ = 5 percentage points absolute | Strict practical equivalence |
| B | δ = 10 percentage points absolute | Lenient equivalence |
| C | accuracy ratio ≥ 0.90 (lower 95% CI) | Relative non-inferiority |

**Lower cost condition.** The "at lower cost" part requires a second,
separately approved rule (e.g., cost per successful task ratio ≤ some margin).
No margin is chosen here. Both margins must be fixed before formal collection.

**Sample size.** Powered from pilot variability (P-09); the 360-trial figure is
feasibility planning only. If the pilot cannot support the chosen δ, report the
achievable precision instead of pretending the test is decisive.

## 3. H2 — falsifiable latency hypothesis (no speed assumption)

H2: first-audio latency(A1) < first-audio latency(A0) for the same model.

- **Unit:** paired per-trial difference (same task, same model, fresh state).
- **Statistic:** median paired difference + bootstrap 95% CI; secondary mean.
- **Falsification:** if the CI includes 0 or is positive, H2 is not supported.
- **Trade-off is explicitly allowed.** A1 may improve accuracy while increasing
  latency. The design does **not** assume otherwise:
  - both accuracy and latency are always reported together;
  - no composite "winner" score is computed without a pre-registered utility;
  - a latency regression is a finding and must not be hidden by an accuracy gain;
  - turn-completion latency is recorded separately because A1 may emit first
    audio earlier but finish the full turn later.
- **No gaming:** the latency path must not skip validation, tools, or grounding to
  win the metric. Any component bypass is logged and is a protocol violation.

## 4. Claim scope

Every claim must name the exact evaluated conditions:

> "Under task set T, models M and S, prompt P, tool set G, database D, and
> environment E, architecture A1 changed task accuracy by X (95% CI …) and
> first-audio latency by Y (95% CI …) relative to A0."

- No "any language model", no universal superiority, no cross-domain claim.
- "Weak-model compensation" is claimed only if H3 passes with the approved margin.
- "Any model" is labelled a long-term architectural ambition / future work, never
  a result of this experiment.
- Exact model IDs, versions, and dates are reported; hosted drift is a limitation.

## 5. Cost methodology

**Primary:** variable cash cost per successful task, USD.

Components, each recorded separately and summed:

1. **LLM:** input tokens × input price + output tokens × output price; cached
   tokens priced separately if the provider reports them. Retries included.
2. **ASR:** charged per audio minute if hosted; local inference recorded as
   compute time, not cash.
3. **TTS:** charged per character/second if hosted; local recorded as compute.
4. **Telephony/hosting:** only when the measured path includes them; fixed costs
   are reported separately and never mixed into variable cost.

**Definitions.**

- `cost_per_attempt` = total variable cost of all attempts / attempts.
- `cost_per_success` = total variable cost of all attempts / successes.
  Failures are included in the numerator; that is the point.
- Prices are snapshotted per run in `freeze.json` (provider, model, unit price,
  currency, date). Price drift is a documented limitation, never silently
  normalised.

**Requirement:** token usage is not currently captured. Add an optional
`usage` field to `LLMResponse`, populated from the provider response
(input/output/cached tokens). This is **additive** and must not change prompts or
generation.

## 6. A0/A1 specification and information parity

Both engines implement one interface and return the same `PipelineResult` /
`TurnRecord` shape.

### Shared inputs (identical in both arms)

- Same audio or transcript, same ASR provider/model/settings.
- Same DB snapshot, repository, and tool registry (same 14 tools, same schemas).
- Same system prompt text (unless a documented A1 orchestration instruction is
  added — then both the text and the rationale are frozen and reported).
- Same `max_tool_iterations`, temperature, top-p, max tokens, timeouts, retry cap.
- Same history window (`max_history_turns`), same task script/user turns.
- Same TTS provider/voice/model; same scoring, failure taxonomy, and logging.
- Neither arm sees the reference transcript, ground truth, task metadata, or
  future turns. No extra retries, no extra DB access, no privileged facts.

### A1 components (all observable, all flag-gated)

1. **Context manager** — resolved entities, constraints, pending intent, from the
   same dialogue history and tool results. No new information sources.
2. **Orchestration** — intent → plan → calls; independent read-only calls may run
   in parallel; bounded retries/timeouts; same total call cap.
3. **Structured tool gateway** — wraps the existing registry: schema validation,
   argument normalization (aliases/quantities/zones), idempotency keys. It may
   reject invalid calls; it may not add facts.
4. **Validation/reliability layer** — grounding against tool output; pre-mutation
   validation; fail-closed provider handling.
5. **Latency path** — token streaming, sentence-level TTS, first-audio priority.
   Must not bypass tools, validation, or grounding.

### Confirmation policy (needs researcher decision)

Preview/confirm before mutation can add a user turn. To keep arms comparable:

- **Option 1 (recommended for the experiment):** the confirmation turn is part of
  the task script and is shown identically to both arms.
- **Option 2:** confirmation is internal validation with no extra user turn.
- **Option 3:** A1 may request confirmation dynamically; then the interaction
  budget differs and the comparison must account for it.

Do not implement until the researcher chooses. A1 must never receive an easier
interaction than A0.

### Switchability

- New config key `architecture.engine: a0|a1` (default `a0`).
- New factory `build_turn_engine(...)` returns the selected engine.
- `ExperimentRunner` selects the engine per cell; A0 construction path stays
  byte-identical.
- Tests assert both engines satisfy the same result contract and that A0 output
  is unchanged on golden inputs.

## 7. Instrumentation and ablation hooks

Additive record fields (A0 and A1): `architecture_version`,
`components_enabled`, `model_id`, `token_usage`, `cost_usd`, `first_audio_ms`,
`turn_completion_ms`, `clarification_turns`.

Per-component spans/events: context, orchestration, each tool, validation,
grounding, first token, first sentence, first audio, completion.

Ablation flags (A1 only, default all-on): `context`, `orchestration`,
`gateway`, `validation`, `latency_path`. Flags are recorded per run. **No
component-level claim is made until ablations are executed.**

## 8. P0 production safety (additive, in `phone.py` + new modules)

1. **Structured intent extraction** — deterministic parse of item(s), quantity,
   zone, confidence, and clarification need from the transcript; validate every
   field against the transcript and DB. Never default a missing quantity to 1.
2. **Field validation** — reject unsupported items/prices/quantities; ask one
   precise clarification.
3. **Preview/confirm before mutation** — pending-order state per caller;
   idempotency key per call; a confirmed order cannot be edited.
4. **Provider health/quota monitoring** — classify 401/402/429/timeouts, log,
   fail closed; no order mutation on provider failure.
5. **Server-side Arabic ASR** — separate phase: requires an audio transport and an
   approved ASR provider; the browser recognizer is not server-grade.

P0 changes must preserve the `/api/chat`, `/api/tts`, `/twilio/*` contracts and
keep `test_phone.py` green. New response fields are additive; `ui.html` handling
is updated only where needed and tested.

## 9. Files and risk to the instrument/data

| File / area | Change | Risk | Mitigation |
|---|---|---|---|
| `src/nbva/agent/pipeline.py` (A0) | **No behavioural change** | High if edited | Frozen; A1 is a new class |
| `src/nbva/arch/` (new) | A1 engine + interface + factory | None (new) | Contract tests |
| `src/nbva/llm/base.py`, `providers.py` | Optional `usage` capture | Medium: shared with A0 | Additive; A0 golden tests |
| `src/nbva/research/runner.py` | Engine selector; unified latency | High: historical comparability | Keep old path for archived runs; version records |
| `src/nbva/research/experiment.py` | Additive CSV columns | Medium: schema | Additive; old runs readable; tests |
| `config/*.yaml` | New keys only; defaults unchanged | Medium: freeze | No changed defaults; freeze records |
| `src/nbva/phone.py` | P0 safety, additive | Medium: production | `test_phone.py` + new tests; contracts preserved |
| `ui.html` | Only if confirmation needs it | Low | Mocked UI tests |
| `runs/`, `data/pilot/` | **No writes** | Data loss | New dev runs go to new dirs |
| Tests | New tests + additive assertions | Low | 418 must stay green |

**Known instrument issue:** the runner currently computes baseline latency as a
sum of spans and streaming latency as `first_audio − endpoint` (P-10). Unifying
this is necessary for a valid comparison and is an instrument change; it will be
versioned and must not be applied silently to historical records.

## 10. Sequencing and gates

- **Phase 1 (no behaviour change):** engine interface, `build_turn_engine`,
  A0 wrapper, `architecture.engine` flag, contract/golden tests, docs.
- **Phase 2:** A1 components one at a time, each flag-gated, each with tests and
  observability; no ablation claims.
- **Phase 3:** P0 safety in `phone.py` (intent, validation, confirmation,
  idempotency, provider monitoring), tests, UI compatibility.
- **Phase 4:** token/cost accounting, unified latency endpoints, additive CSV
  columns, synthetic dev smoke run (labelled dev).
- **Phase 5:** ablation harness + documentation; still no formal trials.

**Acceptance gate:** all existing tests pass (418 baseline), new tests pass, A0
golden output unchanged, no secrets, no freeze/threshold changes, no writes to
existing run data.

## 11. Decisions required before the affected phase

1. H3 margin (A/B/C or other) and the lower-cost rule — before Phase 4.
2. Confirmation policy (Option 1/2/3) — before Phase 2 validation component.
3. Whether A1 may stream TTS in batch and the exact first-audio endpoint — before
   Phase 4.
4. P-10 thresholds — before any formal run.
5. Model pair — before the 2×2 run.
6. Cost scope (ASR/TTS/telephony inclusion) — before Phase 4.
