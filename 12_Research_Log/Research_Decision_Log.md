# Research Decision Log

Dated record of scientific decisions for the project *Egyptian Arabic–English
Code-Switching in Voice Agents*.

**Purpose.** Make the project's scientific history auditable. Every entry records
what was decided, on what day, and why. Changes after the baseline run must be
recorded here before further trials are executed.

**Rule.** The coding agent may not write decisions into this log, alter existing
entries, or fill PROVISIONAL values here. Entries are added by the researcher
only.

---

## Day 1

### D1-01 — Research question fixed
**Decision.** The research question is: "How does increasing Egyptian
Arabic–English code-switching intensity affect the latency, speech-recognition
accuracy, and end-to-end task accuracy of voice agents, and can a targeted
architecture mitigate the resulting performance degradation?"
**Rationale.** Defines the study as a relationship between one manipulated
variable and a small set of performance outcomes, with mitigation as a
second-stage question.

### D1-02 — Project scope and explicit exclusions fixed
**Decision.** In scope: a voice agent for a fictional Egyptian restaurant, users
speaking Egyptian Arabic, English, or code-switched speech, controlled business
tasks against a fixed database. Primary outcomes: ASR accuracy, end-to-end task
accuracy, response latency. Secondary diagnostic outcomes: context-resolution
accuracy, database/tool accuracy, error type / failure mechanism.
Not primary subjects: TTS quality, voice attractiveness, general conversational
intelligence, emotion recognition, other Arabic dialects, multilingual text
chat, general-purpose translation, comparing dozens of models, real restaurant
operations.
**Rationale.** The exclusions prevent scope creep and prevent the study from
being framed as a general multilingual-LLM evaluation.

### D1-03 — Fictional restaurant adopted as controlled environment
**Decision.** The controlled environment is a fictional restaurant that will
eventually contain menu items, ingredients, prices, availability, customization
options, delivery zones, delivery times, business hours, promotions, and current
order state.
**Rationale.** A fictional environment makes the database state fully known,
fixed, and reproducible, which is required for the database to function as a
controlled variable.
**Status of contents.** Not authored. Database design is deferred.

### D1-04 — Seven task families fixed
**Decision.** Task families are: (1) Information, (2) Price & availability,
(3) Delivery, (4) Order creation, (5) Order modification, (6) Multi-turn
context, (7) Combined tasks.
**Rationale.** The families span the business task space from read-only queries
to stateful multi-step operations, which is required to observe
context-resolution and order-state failure mechanisms.
**Open.** Number of tasks per family and family balance are not set.

### D1-05 — Trial defined as the experimental unit
**Decision.** The basic experimental unit is the "trial": one predefined user
request/conversation executed under a specific code-switching condition.
**Rationale.** Trial-level analysis with the underlying task held constant is
what allows an outcome difference to be attributed to code-switching intensity.
**Open.** Trial counts, repetitions per cell, and run ordering are not set.

### D1-06 — Code-switching intensity measure fixed
**Decision.** The primary linguistic measure is English lexical-token
proportion = English lexical tokens / total lexical tokens × 100. This refers to
**linguistic lexical tokens, not model/subword tokens**.
**Rationale.** The measure must be independent of any model's internal
tokeniser, otherwise the manipulated variable would be confounded with the ASR
or LLM implementation being evaluated.
**Open.** The tokenisation and annotation procedure used to apply the formula is
not specified. Numerical bands are not set (see D1-07).

### D1-07 — Five conditions intended; boundaries left provisional
**Decision.** Five increasing conditions are intended: C0 = 0%, C1 = Low,
C2 = Moderate, C3 = High, C4 = Very high. The exact numerical boundaries of
C1–C4 **remain provisional** until the methodology is finalized.
**Rationale.** Fixing band boundaries before the task set and annotation
procedure exist would be arbitrary. C0 is the only fully specified condition,
because 0% follows from the definition rather than from a chosen threshold.
**Open.** C1–C4 numerical ranges; whether C0 covers pure Egyptian Arabic, pure
English, or both as sub-conditions.

### D1-08 — Matched-task principle adopted
**Decision.** Different code-switching conditions must preserve the same
underlying task meaning; only the language mixture changes. Example: "عايز
اتنين تشيكن برجر من غير جبنة." versus "I want اتنين chicken burgers من غير
cheese."
**Rationale.** Holding meaning constant is what isolates code-switching
intensity as the cause of any performance difference.
**Open.** Formal matching procedure and meaning-equivalence verification method.

### D1-09 — Dependent variables and their primary definitions fixed
**Decision.** ASR accuracy is measured primarily by Word Error Rate (WER).
End-to-end task accuracy is whether the agent actually performs the intended
task correctly. Latency is time from the end of the user's speech to the
beginning of the agent's response, with component timings (ASR, understanding,
database/tool operations, response generation, TTS) to be logged later where
technically possible as diagnostics.
**Rationale.** These three are the primary outcomes named in the research
question; component timings are diagnostic only.
**Open.** WER normalisation convention, task-accuracy rubric, latency
instrumentation points and timing source.

### D1-10 — Ten-category error classification adopted
**Decision.** Failed trials are classified where possible as: (1) ASR error,
(2) Intent/understanding error, (3) Entity/slot error, (4) Database retrieval
error, (5) Tool-call error, (6) Context-resolution error, (7) Order-state
error, (8) Latency/timeout, (9) Response-generation error, (10) Other.
**Rationale.** The classification is the mechanism that converts observed
failures into identified failure mechanisms, which in turn drive intervention
design.
**Open.** Decision procedure, annotator, agreement handling, treatment of
multi-cause failures.

### D1-11 — Baseline architecture concept fixed
**Decision.** The baseline is the straightforward pipeline: User speech → ASR
→ Language understanding / LLM → Business database + tools → Response
generation → TTS → User. The baseline must be a reasonable implementation, not
intentionally weakened.
**Rationale.** A deliberately weakened baseline would make degradation
uninterpretable; failures must be attributable to the code-switching condition.
**Open.** Specific ASR, LLM, and TTS technologies; deployment topology.

### D1-12 — Intervention declared non-predetermined
**Decision.** The intervention is not predetermined. The process is: baseline
experiment → observe failures → classify failures → identify failure mechanisms
→ design targeted architecture → implement intervention → test intervention →
compare results. The Aether Veil architecture may become the intervention, but
it must be derived from observed failure mechanisms and must not be designed in
advance to produce a positive result.
**Rationale.** A pre-designed intervention would be confirmation-seeking; the
scientific claim depends on the mechanism being discovered from data.
**Open.** Everything about the intervention's design. No intervention
architecture has been specified.

### D1-13 — Hypothesis stated
**Decision.** Increasing Egyptian Arabic–English code-switching intensity will
reduce speech-recognition accuracy and end-to-end task accuracy and may
increase response latency. A targeted architecture designed around observed
failure mechanisms will mitigate at least some of this degradation.
**Rationale.** Note that the latency effect is stated as "may increase" while
the accuracy effects are asserted; this asymmetry is part of the Day 1
hypothesis as recorded.
**Open.** Statistical analysis plan — tests, effect sizes, significance
thresholds, baseline-versus-intervention comparison procedure.

### D1-14 — Controlled variables fixed
**Decision.** Held constant during the baseline experiment: restaurant
database, task definitions, expected answers, system prompt, model versions,
model settings, ASR configuration, tool definitions, evaluation criteria, audio
conditions, measurement procedure, and hardware/runtime environment where
practical. The principal experimental variable is code-switching intensity.
**Rationale.** Identifies the single degree of freedom the study varies.
Any change to a controlled variable after the baseline run requires a log entry
before further trials are executed.

### D1-15 — Experimental boundaries fixed
**Decision.** The research concerns Egyptian Arabic–English code-switching in
controlled voice-agent business interactions. Findings are not automatically
generalized to all Arabic speakers, all Egyptian speakers, all voice agents, all
languages, all AI systems, or all commercial applications.
**Rationale.** Bounds the external validity claims available to the write-up.

### D1-16 — Project skeleton created
**Decision.** A numbered folder structure was created:
`01_Research_Design`, `02_Literature`, `03_Business_Environment`,
`04_Task_Dataset`, `05_Audio`, `06_Baseline_Agent`, `07_Experiments`,
`08_Intervention`, `09_Analysis`, `10_Figures_Tables`, `11_Paper`,
`12_Research_Log`.
**Rationale.** Separates design, data, code, experiment records, analysis, and
writing so that a controlled variable can be located and frozen unambiguously.
**Note.** This is an organizational decision, not a scientific one.

### D1-17 — Division of labour between researcher and coding agent fixed
**Decision.** The researcher owns all scientific decisions. The coding agent
owns project setup and implementation only, and may not change, reinterpret, or
invent a scientific decision. If a scientific decision appears to need changing,
the coding agent flags it for the researcher instead of changing it. Items
marked PROVISIONAL are not to be filled in by the coding agent.
**Rationale.** Keeps interpretation of the evidence with the researcher and
prevents implementation convenience from shaping the methodology.

---

## Future Methodological Changes

### 2026-11-18 — Primary variable changed to architecture × model capability

**Recorded at the researcher's explicit direction during the 2026-11-18 working
session; transcribed by the coding agent on instruction. The researcher should
edit this wording if it does not match their intent.**

**Decision.** The project's independent variables are now **agent architecture**
(conventional sequential baseline A0 vs reliability/efficiency architecture A1)
and **language-model capability** (weak/inexpensive vs strong). The primary
outcomes are task accuracy, then latency and cost. Egyptian Arabic–English
code-switching is an application-environment property, not the manipulated
variable. Fine-tuning is not an approved current factor. The full design is
`01_Research_Design/Current_Direction.md`; the operational protocol is the
rewritten `EXPERIMENT_PROTOCOL.md`.

**Supersedes.** D1-01 (research question), D1-05's "code-switching condition"
clause, D1-06 (code-switching intensity measure), D1-07 (five C0–C4 conditions).

**Rationale.** The researcher's core thesis is that a reliability-oriented
architecture can improve effectiveness and efficiency and get the best result out
of any model, including a weak one. Code-switching intensity does not test that
thesis.

**Disposition of old material.** The Day 1 document, the archived protocol
(`01_Research_Design/_superseded/EXPERIMENT_PROTOCOL_v1_code_switching.md`), the
C0–C4 measure, and the synthetic pilot data are preserved for auditability and
possible secondary analysis. They are not the current design and must not be
described as such.

**Affects collected data?** No — no formal research data has been collected.

### 2026-11-18 — Operational decisions for the 2 × 2

**Recorded from the researcher's direct answers during the 2026-11-18 session;
transcribed by the coding agent. The researcher should edit the wording if it
does not match their intent.**

**Decision 1 — the H3 criterion is relative, not absolute.** Operational form:
the lower bound of the 95% confidence interval on the ratio
`p(M·A1) / p(S·A0)` must be ≥ 0.90. "Approaches" is claimed only when that bound
holds. A ratio significantly above 1.0 is not reported as superiority without a
separate test. Rationale: the ratio states the claim in the researcher's own
terms — the weak model retains at least 90% of the strong model's success rate —
and is scale-free across task families.

**Consequence that must be recorded.** The ratio is stricter in absolute terms
when accuracy is low (0.90 of p = 0.70 is a 7 pp gap; of p = 0.90 it is a 9 pp
gap) and looser at the ceiling (10 pp at p = 1.0). The confidence-interval method
and the boundary rule must be fixed before formal collection; tracked as P-11.

**Correction 2026-11-18.** An earlier version of this entry said the ratio is
"undefined when p(S·A0) = 1.0". That was wrong: a denominator of 1.0 is well
defined. The real issue is that the denominator's variance is then estimated as
zero, which makes delta-method intervals anti-conservative. See P-11 in
`12_Research_Log/Pending_Decisions_For_Researcher.md` and
`08_Intervention/STATISTICAL_DESIGN_PAIRED.md` §5.

**Decision 2 — confirmation belongs to the task script.** Where a task requires
preview/confirm before a mutation, the confirmation turn is written into the
frozen task and is shown identically to A0 and A1. No arm receives a longer or
shorter interaction budget, and A0 is not excluded from confirming.

**Decision 3 — cost scope is LLM + hosted TTS.** These are the two real variable
cash costs on the measured path. Browser ASR and any local inference are recorded
as compute time, never as cash. Telephony and hosting are fixed costs, reported
separately and excluded from variable cost per successful task.

**Decision 4 — "first audio" means first audio ready.** In batch runs A0 stamps
the moment full synthesis completes; A1 stamps the moment its first
sentence-length chunk is synthesized. Both mean "the first audio a caller could
hear". Full-utterance-ready is a different measurement and is not used as
first-audio latency.

**Affects collected data?** No — no formal research data has been collected.

| Date | Modifies | Change | Rationale | Affects collected data? |
|------|----------|--------|-----------|--------------------------|
| 2026-11-18 | D1-01, D1-05, D1-06, D1-07 | Architecture × model capability replaces code-switching intensity as the primary design | Architecture can compensate for model weakness; code-switching is environment, not IV | No |
| 2026-11-18 | D1-01 (H3) | H3 margin set as accuracy ratio ≥ 0.90 on the lower 95% CI bound | Scale-free; matches "retains ≥90% of the strong model's success rate" | No |
| 2026-11-18 | — | Confirmation turn fixed inside the task script, identical for A0 and A1 | Keeps the interaction budget equal across arms | No |
| 2026-11-18 | — | Cost scope = LLM + hosted TTS; compute time and fixed costs excluded | Only cash spend on the measured path counts as variable cost | No |
| 2026-11-18 | — | First-audio endpoint = first audio ready (per-arm synthesis point) | Equals what a caller hears first; fair to both arms | No |

---

## 2026-11-19 — Production phone agent: the model drives the turn

Full evidence and change list: `08_Intervention/PRODUCTION_AGENT_FIX.md`.

**Decision 5 — the deployed prototype runs the model on every turn.** The
keyword/template order path (`_direct_order_response`) no longer answers first; it
is behind `phone.direct_order_path`, default `false`. The mandatory
"reasoning pass" — a complete LLM call per turn whose output was discarded — is
deleted. Rationale: the prototype was answering the most common utterances
without consulting the model at all, which is what "scripted" meant, and paying a
full extra round trip per turn for nothing. The template layer is kept and tested
because it encodes real safety rules (no invented item, no invented quantity, no
item substitution).

**Decision 6 — production uses a separate persona prompt.**
`config/prompts/phone_agent_prompt.md` is loaded by `PhoneBridge` only. The
research prompt `config/prompts/baseline_system_prompt.md` remains the controlled
variable of the experiment and is not edited. Keeping one prompt for both was a
confound in the other direction: the deployed agent was telling callers it was
being evaluated.

**Decision 7 — state injection is production-only, and optional.**
`process_turn` accepts `context_note=None`. Production supplies the current order
(every field derived from tool output); the research path supplies nothing, so the
message list and the grounding check are unchanged. This is deliberately **not**
implemented as an A1 component: A1's context manager must be measured as part of
the intervention, and giving A0 the same state note would erase that contrast.

**Decision 8 — `api/index.py` must not choose the model silently.** An unset
`OPENAI_MODEL` now logs a warning and `/healthz` reports the live model, provider,
prompt file and engine. Model selection for the prototype is the owner's, not a
default in code.

**Affects collected data?** No — no formal research data has been collected, and
the research path's behaviour is unchanged (`context_note=None` reproduces the
previous message list exactly).

| Date | Modifies | Change | Rationale | Affects collected data? |
|------|----------|--------|-----------|--------------------------|
| 2026-11-19 | — | Production answers every turn through the model; template path flag-gated off | The template layer was intercepting the common utterances, so the model never ran | No |
| 2026-11-19 | — | Removed the discarded per-turn LLM "reasoning pass" | One useless round trip per turn; refusal on any failure | No |
| 2026-11-19 | — | Production persona prompt, separate from the research prompt | The research prompt told the deployed agent it was a trial subject | No |
| 2026-11-19 | — | `context_note` (optional) carries order state to the model; research passes none | The model was amnesiac about the order it had built | No |
| 2026-11-19 | — | Fries no longer default to quantity 1 | Invented a line the caller never asked for | No |
| 2026-11-19 | — | Out-of-scope veto narrowed to named unrelated domains | It refused first-turn Arabic greetings | No |
| 2026-11-19 | — | `/api/chat` returns speech chunks; client synthesizes chunk 1 first | First audio waited for chat completion plus full-reply synthesis | No |

