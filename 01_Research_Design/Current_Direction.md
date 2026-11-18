# Current Research Direction

**Status:** AUTHORITATIVE as of 2026-11-18. Supersedes the Day 1
code-switching framing (`01_Research_Design/Day_01_Research_Design.md`,
`12_Research_Log/Research_Decision_Log.md` D1-01/D1-06/D1-07) and the archived
protocol `01_Research_Design/_superseded/EXPERIMENT_PROTOCOL_v1_code_switching.md`.

**Recorded at the researcher's explicit direction (2026-11-18).** The researcher
owns this decision; the coding agent transcribed it. The researcher should edit
wording in the decision log if needed.

---

## 1. What the project is now

The object of study is an **agent architecture** that gets the best possible
result out of a language model — including a weak or inexpensive model — by
improving:

- **effectiveness:** task accuracy, grounding, context retention, fewer failures;
- **efficiency:** response latency, tool-call overhead, token and cost overhead.

The research is not about Egyptian Arabic–English code-switching. Egyptian
Arabic–English restaurant conversation is only the **application environment**.
Users still speak Arabic, English, or both; that is a property of the setting,
not the independent variable. The older C0–C4 material is preserved as secondary
/ historical work and must not be cited as the current design.

The dual-track goal is unchanged: a scientifically defensible study and a
deployable reliability-oriented agent platform.

## 2. Working research question (primary)

> How do agent architecture and language-model capability independently and
> jointly affect task accuracy, response latency, and cost of Egyptian
> Arabic–English conversational voice agents in tool-based tasks?

Sub-questions:

1. Does the reliability/efficiency architecture improve task accuracy over a
   conventional sequential baseline for the same model?
2. Does it reduce first-audio latency and turn-completion latency?
3. Can a **weak model + optimized architecture** match a **strong model +
   baseline** on task accuracy at lower latency/cost?
4. Is the architecture benefit larger for weaker models (interaction effect)?
5. Which failure types does the architecture reduce?

A provisional title, subject to researcher approval:

> Evaluating the Effects of Agent Architecture and Language-Model Capability on
> the Accuracy, Latency, and Cost of Conversational Voice Agents

## 3. Core hypothesis

A reliability-oriented architecture — context management, structured tool
gateway, validation, orchestration, and latency-path optimization — can
compensate for model weakness. Operational form:

- **H1:** optimized architecture raises task accuracy for a fixed model.
- **H2:** optimized architecture lowers first-audio latency for a fixed model.
- **H3:** a weak model + optimized architecture approaches a strong model +
  baseline on task accuracy.
- **H4:** the architecture gain is larger for the weaker model (interaction).

No claim of universal superiority; conclusions are limited to the models,
prompts, tools, tasks, configurations, and environment actually tested.

## 4. Experimental design — candidate, not frozen

**2 × 2 factorial:**

| Factor | Levels |
|---|---|
| Model capability | weak/inexpensive model (M); strong model (S) |
| Agent architecture | conventional sequential baseline (A0); optimized reliability/efficiency architecture (A1) |

Four cells: M·A0, M·A1, S·A0, S·A1.

**Model pair is not approved.** Choose only after comparing tool-calling
support, context window, rate limits, availability, pricing, and comparable
settings. Do not choose by popularity. Both models must support the same tool
interface and run with the same prompts, tools, database, ASR, and TTS.

**Optimized architecture A1 components (research concepts, not claimed novel):**

1. **Context manager** — resolved entities, constraints, multi-turn state;
   prevents re-asking and context loss.
2. **Orchestration layer** — intent → plan → tool calls; parallel independent
   calls; bounded retries and timeouts; circuit breakers.
3. **Controlled structured tool gateway** — schema-validated arguments, tool
   allowlist, deterministic normalization (item aliases, quantities, zones),
   idempotency keys.
4. **Validation / reliability layer** — grounding checks against tool output,
   preview and confirmation before mutation, fail-closed provider handling.
5. **Latency path** — endpointer, streaming hypotheses, speculative **read-only**
   calls, token streaming, sentence-level TTS, first-audio priority.

Changing components together evaluates the **combined architecture**. Claims
about individual components require ablations. Ablation hooks must exist before
such claims are made.

## 5. Controlled variables

Held constant across all four cells: ASR provider/model/settings; TTS
provider/voice/settings; task set and ground truth; database snapshot; tool set;
temperature/top-p and max tokens where the architecture does not require a
difference; environment (hardware, network class, weekday pinned); scoring and
failure taxonomy. Every value is recorded in `freeze.json`.

## 6. Metrics

**Primary — task accuracy:** deterministic final-state assertions (order lines,
quantities, totals, delivery zone, status, required tools). Wording is never
graded; the database state is the source of truth.

**Secondary — latency:** first-audio latency (user-turn end → first response
sample) and turn-completion latency (user-turn end → final response ready).
The two are never mixed. **Cost** per successful task where measurable.

**Diagnostic:** tool-selection accuracy, tool-argument accuracy, context
retention, grounding violations, clarification rate, failure category, token
and call overhead.

## 7. Validity threats

Hosted-model drift (record exact model IDs and dates); rate limits and provider
outages (record, fail closed, never rewrite results); prompt sensitivity;
network variance; evaluator subjectivity (assertion-based scoring, human
override only with a recorded rule); cost accounting assumptions. These are
limitations to report, not hidden.

## 8. Sample size

The prior 30 scenarios × 3 repetitions × 4 cells = 360 trials is
**feasibility planning only**, not an approved sample size. Pilot variability
informs the final number. Trial counts and exclusions must be fixed before
formal collection.

## 9. Integrity rules

- Never invent data or call synthetic pilot material findings.
- Do not change the question, factors, tasks, scoring, latency definitions,
  sample size, or exclusions after seeing results.
- Log approved methodological changes in `Research_Decision_Log.md`.
- Keep baseline and intervention separable and reproducible.
- Engineering evidence (tests, demos, deployments) is not research evidence.

## 10. Relationship to the existing repository

- The instrument (`src/nbva/`), task environment, and reliability contract are
  reused; the study is re-aimed, not rebuilt from zero.
- C0–C4 conditions, the code-switching measure, and the old pilot data remain in
  the repository as superseded/secondary material. Do not delete them and do not
  present them as the current design.
- `EXPERIMENT_PROTOCOL.md` is rewritten for this design. The old protocol is
  archived under `01_Research_Design/_superseded/`.
