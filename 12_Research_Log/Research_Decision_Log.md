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

*No methodological change has been decided yet. This section is intentionally
empty and must remain empty until the researcher approves a specific change.*

Record each change as: date, which D1 entry it modifies or supersedes, the new
decision, the rationale, and whether it affects comparability with data already
collected.

| Date | Modifies | Change | Rationale | Affects collected data? |
|------|----------|--------|-----------|--------------------------|
| — | — | — | — | — |
