# Phase 2 — A1 component specification

**Status:** ACTIVE. Stage 2a implemented; 2b–2e specified, not implemented.
**Date:** 2026-11-18
**Authority:** approved by the researcher (Phase 2), with conditions recorded in
§1. This document is the required "changes that could affect experimental
validity" record for each stage.

---

## 1. Invariants the researcher set (2026-11-18)

1. **Deterministic-first.** The context manager and orchestrator are deterministic.
   No extra LLM call is added. Adding one requires a later experiment to justify it.
2. **H2 stays falsifiable.** Every component's latency overhead is **measured**,
   never assumed to be zero.
3. **Tool parity.** A0 and A1 keep equivalent tools, database access, and task
   information. A1 may improve tool-call *structure and reliability*; it may not
   receive additional task knowledge.
4. **Audio timing.** Time to first playable audio and time to complete response
   audio are recorded **separately**, and each architecture keeps its real
   streaming behaviour.
5. **Incremental.** Components land one at a time, flag-gated and observable, with
   ablation hooks. At the end, the complete A1 must be selectable and **no
   component may silently fall back to A0**.
6. No formal trials, no task-script changes, no sample size, until the analysis
   plan and remaining decisions are reviewed.

## 2. What "deterministic" means here, precisely

A component is deterministic if, given the same session state and the same model
output, it produces the same result **and** it performs no model inference. It may
read the database, the dialogue history, and tool results. It may not:

- call an LLM, or embed a model in any other form;
- invent a fact, price, quantity, zone, or identifier;
- fill a missing user-specified value with a default;
- consult the task definition, ground truth, reference transcript, or later turns.

Every deterministic component is therefore also **auditable without a model in the
loop**, which is why each has unit tests with no network.

## 3. Components

### 3a. Structured tool gateway — `src/nbva/arch/gateway.py` (IMPLEMENTED)

Wraps the **same** `ToolRegistry`, exposing the same `llm_schemas()` so the model
sees an identical tool list.

**Does**

- validates arguments against the tool's own JSON schema **before** dispatch
  (required fields, types, enum, minimum/maximum, `minItems`, unknown properties);
- coerces a numeric string to an integer where the schema says integer and the
  string is an integer literal (e.g. `"2"` → `2`); rejects anything else;
- returns a structured `VALIDATION_FAILED` with the failing field so the model can
  repair its call in the next loop iteration;
- suppresses a **duplicate `create_order`** within one turn (same normalized
  arguments, already succeeded), returning the original result with
  `reused: true`;
- measures validation time and dispatch time per call.

**Does not**

- resolve item names, prices, aliases, or zones — the registered tools already do
  that, and duplicating it would create a second source of truth;
- add, remove, or reorder tools;
- change a value the user or model supplied, beyond the numeric-string coercion
  above;
- add knowledge the model did not have.

**Parity evidence required:** `gateway.llm_schemas() == registry.llm_schemas()`.

### 3b. Context manager — `src/nbva/arch/context.py` (STAGE 2b)

Maintains structured session state derived **only** from the dialogue history and
tool results: the active order id, item ids seen with their canonical names and
prices, explicit quantities stated, delivery/pickup mode, zone, and whether the
last assistant turn asked a clarification (and about what).

**Does:** produce a compact, deterministic state summary and a pending-question
flag. Repeats of an already-answered question are detected mechanically.

**Does not:** infer intent semantically, resolve pronouns, fill missing
quantities, or decide what the user "probably" wants.

**Latency cost:** O(turns × tool results), no I/O. Measured, not assumed zero.
**Ablation flag:** `context`.

### 3c. Validation / reliability layer — `src/nbva/arch/validation.py` (STAGE 2c)

**Does:** grounding check against tool output (reusing A0's rule); pre-mutation
check that a line has an item and an explicit quantity; fail-closed handling when
a provider returns an error (no order mutation on failure); idempotency accounting
for the gateway.

**Does not:** rewrite the response with a model, or emit a reply containing a fact
that no tool returned.

**Latency cost:** regex and set operations over one short string. Measured.
**Ablation flag:** `validation`.

### 3d. Orchestration — `src/nbva/arch/orchestrator.py` (STAGE 2d)

A tool loop that mirrors `run_tool_loop` exactly, with three differences:
independent **read-only** calls in one model response may execute concurrently;
each call is routed through the gateway; per-iteration timings are recorded.

**Does:** execute the calls the model requested, in the model's order for anything
mutating; preserve the transcript order by appending results in request order even
when execution was concurrent; bound retries of transient tool errors; enforce
timeouts.

**Does not:** add a call the model did not request, reorder mutations, retry an LLM
call, or exceed the same `max_tool_iterations` as A0.

**Latency cost:** may *reduce* wall time when a response contains several reads;
adds thread-pool overhead otherwise. Both measured.
**Ablation flags:** `orchestration`, and `parallel_reads` (default off until a
determinism test proves result ordering is stable).

### 3e. Latency path — `src/nbva/arch/latency.py` (STAGE 2e)

Sentence-level TTS: synthesize the first sentence, start playback, synthesize the
remainder while the first is playing.

**Does:** stamp `first_playable_audio` at the first synthesized chunk and
`response_audio_complete` when the whole reply has been synthesized and played.

**Does not:** skip validation or grounding. Validation runs on the full text before
any audio is emitted — it is deterministic and costs microseconds, so there is no
latency argument for deferring it.

**Ablation flag:** `latency_path`.

**A0 contrasts deliberately:** A0 synthesizes the entire reply, then plays.
Its `first_playable_audio` equals its synthesis-complete instant.

## 4. Instrumentation

### 4.1 Per-component timing

Each component records `*_ms` spans in the turn record. A0 records none of them
(no components exist). Components are timed with `time.perf_counter()` on the same
process, so spans are comparable within a turn and summable.

**Ablation measurement rule.** Every component must report its own cost even when
it changes nothing about the outcome — a validator that approves every call still
costs time, and hiding that would flatter A1.

### 4.2 Audio timing fields (new, additive)

| Field | A0 | A1 |
|---|---|---|
| `first_playable_audio_ms` | synthesis complete | first sentence synthesized |
| `response_audio_complete_ms` | playback finished | playback finished |
| `tts_chunk_count` | 1 | number of sentences |
| `tts_chunk_ready_ms[]` | 1 entry | per-sentence |

These are **additive** to `LatencyMarkers` and the turn record; existing fields and
meanings do not change, and A0 records them with the values it actually has. An
A0 record written before this change stays readable in the same CSV.

### 4.3 Component record

`TurnRecord.extra["architecture"] = {"engine": "a1", "components": {...}, "spans": {...}}`.
Additive. A0 writes `{"engine": "a0", "components": {}}` once the stamping stage
lands; until then its records carry no key and readers must default to A0.

## 5. Tool parity contract

A test enforces, per stage:

- `set(A1 tool names) == set(A0 tool names)`;
- `A1.llm_schemas() == A0.llm_schemas()`, byte-identical;
- the same database snapshot, same `business_weekday`, same `max_tool_iterations`;
- A1 receives **no** task id, reference transcript, ground truth, or future turn
  that A0 does not receive.

Any stage that cannot satisfy this is rejected, not reworked into the design.

## 6. No silent fallback

The final stage adds a test that:

1. builds the app with `architecture.engine: a1`;
2. asserts the engine reports `a1` and all five components enabled;
3. runs a full turn and asserts the component record shows every component
   executed (not skipped);
4. disables each component in turn and asserts the record shows the flag honored;
5. asserts that an exception inside a component **propagates as an error** rather
   than being swallowed into A0-equivalent behaviour.

## 7. Stage order and validity impact

| Stage | Component | Validity / data impact |
|---|---|---|
| 2a | Gateway | New module; A0 unaffected. Changes tool-call outcomes only for malformed calls, which A0 rejects later and less precisely. |
| 2b | Context | Adds a deterministic summary to the A1 prompt. **Prompt now differs between arms** — must be frozen, hashed, and reported. |
| 2c | Validation | May replace a reply that contains an ungrounded number. That is the intervention; it must be counted, not hidden. |
| 2d | Orchestration | Changes tool execution order only for concurrent reads. Transcript order preserved, so the model's next input is unchanged. |
| 2e | Latency path | Adds audio timing fields; changes when playback starts for A1 only. A0 untouched. |
| 2f | Assembly | Wires the engine, adds the no-fallback test suite. No scientific value changes. |

**Stage 2b is the one that touches the instrument most.** A1 will send the model a
different prompt from A0. That is intended — it is the architecture — but the
prompt text, its SHA-256, and the generation rule must be frozen and reported, or
the comparison is uninterpretable.

## 8. Explicitly out of scope for Phase 2

- any second LLM call (context resolution, planning, re-ranking, self-check);
- fine-tuning, embeddings, retrieval, vector stores;
- changes to the task script, the scoring rules, the failure taxonomy;
- changes to `src/nbva/agent/pipeline.py` (A0 stays frozen);
- formal trials, sample size, latency thresholds.
