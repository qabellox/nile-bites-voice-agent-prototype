# Day 01 — Research Design

**Project:** Egyptian Arabic–English Code-Switching in Voice Agents
**Date of this document:** Day 1
**Status:** Initial design record. All items below reflect decisions made by the
researcher on Day 1. Anything not yet fixed is marked **PROVISIONAL**.

---

## 1. Research Question

> How does increasing Egyptian Arabic–English code-switching intensity affect the
> latency, speech-recognition accuracy, and end-to-end task accuracy of voice
> agents, and can a targeted architecture mitigate the resulting performance
> degradation?

This is the primary research question of the project. All sub-questions,
measures, and analyses are subordinate to it.

---

## 2. Research Scope

**In scope.** A voice agent serving a fictional Egyptian restaurant. Users speak
Egyptian Arabic, English, or Egyptian Arabic–English code-switched speech. The
agent must understand requests and perform controlled business tasks using a
fixed database.

**Primary outcomes.**

1. ASR accuracy
2. End-to-end task accuracy
3. Response latency

**Secondary diagnostic outcomes.**

4. Context-resolution accuracy
5. Database/tool accuracy
6. Error type / failure mechanism

**Explicitly NOT primary subjects of study.**

- TTS quality
- Voice attractiveness
- General conversational intelligence
- Emotion recognition
- Other Arabic dialects
- Multilingual text chat
- General-purpose translation
- Comparing dozens of AI models
- Real restaurant operations

These exclusions are scope boundaries, not ranked priorities. Where one of these
turns out to be technically unavoidable (for example TTS, which sits inside the
latency measurement), it is treated as a controlled component rather than an
outcome variable.

---

## 3. Experimental Environment

The controlled environment is a **fictional restaurant**. It is fictional by
design, so that the database state is fully known, fixed, and reproducible.

The environment will eventually contain:

- menu items
- ingredients
- prices
- availability
- customization options
- delivery zones
- delivery times
- business hours
- promotions
- current order state

**PROVISIONAL:** The database contents have not been authored. Building it is a
later step and must be preceded by researcher approval of its exact contents,
since the database is a controlled variable (see Section 14).

---

## 4. Task Families

The task set is organised into seven families:

1. Information
2. Price & availability
3. Delivery
4. Order creation
5. Order modification
6. Multi-turn context
7. Combined tasks

**PROVISIONAL:** The number of trials per family, the specific task instances
within each family, and the balance between families have not been set.

---

## 5. Experimental Unit

The basic experimental unit is a **trial**.

> A trial is one predefined user request/conversation executed under a specific
> code-switching condition.

Consequences of this definition, as decided on Day 1:

- The unit of analysis is the trial, not the turn and not the participant.
- One trial yields one value per dependent variable (Section 9).
- Comparisons between code-switching conditions are made at trial level, with the
  underlying task held constant (Section 8).

**PROVISIONAL:** The number of trials, repetition count per cell, and any
randomisation or ordering scheme have not been set.

---

## 6. Code-Switching Intensity

The primary linguistic measure of code-switching intensity is:

```
English lexical-token proportion =
    English lexical tokens / total lexical tokens × 100
```

**Interpretation rules (Day 1).**

- This refers to **linguistic lexical tokens**, NOT model/submodel tokens.
  Tokenisation for this measure is a linguistic annotation task, independent of
  any model's internal tokeniser.
- "Total lexical tokens" is the denominator for every utterance under
  evaluation.

**PROVISIONAL:** The exact numerical bands are **not finalized**. No cut-off
values are recorded in this document. The tokenisation and annotation procedure
used to apply the formula has not yet been specified, and it is a controlled
variable (Section 14).

---

## 7. Experimental Conditions

Five increasing conditions are intended:

| ID | Label | English lexical-token proportion |
|----|-------|-----------------------------------|
| C0 | 0% | fixed at 0% by definition |
| C1 | Low | **PROVISIONAL** |
| C2 | Moderate | **PROVISIONAL** |
| C3 | High | **PROVISIONAL** |
| C4 | Very high | **PROVISIONAL** |

The exact numerical boundaries of C1–C4 **must remain PROVISIONAL** until the
methodology is finalized. C0 is the only fully specified condition, because 0% is
fixed by the definition of the measure rather than by a chosen threshold.

**PROVISIONAL:** Whether C0 is pure Egyptian Arabic, pure English, or is
represented as more than one sub-condition has not been decided. Also
undecided: how many trials fall in each condition, and whether trials are
assigned to conditions by construction or by measurement of realised
intensity.

---

## 8. Matched-Task Principle

Different code-switching conditions should preserve the **same underlying task
meaning**. Only the language mixture changes.

Example given by the researcher:

- Low mixing:
  > "عايز اتنين تشيكن برجر من غير جبنة."
- Higher mixing:
  > "I want اتنين chicken burgers من غير cheese."

The meaning must remain equivalent while the language mixture changes.

**Consequence.** For any matched set, the expected agent outcome, the relevant
database records, and the pass/fail criteria are identical across C0–C4. This
is what allows a difference in outcome to be attributed to code-switching
intensity rather than to a difference in task difficulty.

**PROVISIONAL:** The formal matching procedure, the per-family matched sets, and
the verification method for meaning-equivalence have not been specified.

---

## 9. Dependent Variables

### 9.1 ASR accuracy

- **Primary metric:** Word Error Rate (WER).

**PROVISIONAL:** The reference transcript convention, normalisation policy
(casing, punctuation, number formatting, Arabic orthographic variants), and the
segmentation of multi-turn trials have not been finalized.

### 9.2 End-to-end task accuracy

Whether the agent actually performs the intended task correctly.

**PROVISIONAL:** The pass/fail rubric, whether it is binary or graded, and
whether human judgement is used have not been finalized.

### 9.3 Latency

**Primary definition:** time from the end of the user's speech to the beginning
of the agent's response.

**Component timings** should later be logged, where technically possible, for:

- ASR
- understanding
- database/tool operations
- response generation
- TTS

The component timings are diagnostic, not a primary outcome.

**PROVISIONAL:** The timing instrumentation points, the timing source
(client-reported vs. server-logged), repetition policy, and how TTS time is
accounted for in the primary latency definition have not been finalized.

---

## 10. Error Classification

When a trial fails, the failure is classified where possible into one of:

1. ASR error
2. Intent/understanding error
3. Entity/slot error
4. Database retrieval error
5. Tool-call error
6. Context-resolution error
7. Order-state error
8. Latency/timeout
9. Response-generation error
10. Other

This classification serves the diagnostic outcomes in Section 2 and is the
mechanism that drives the intervention design described in Section 12.

**PROVISIONAL:** The decision procedure, the annotator, inter-rater agreement
handling, and the treatment of ambiguous or multi-cause failures have not been
finalized.

---

## 11. Baseline Architecture

The initial baseline concept is the following straightforward pipeline:

```
User speech
→ ASR
→ Language understanding / LLM
→ Business database + tools
→ Response generation
→ TTS
→ User
```

Requirement: the baseline must be a **reasonable, straightforward
implementation, not an intentionally weakened one**. Any failure observed under
the baseline is therefore attributable to the code-switching condition rather
than to a deliberately handicapped system.

**PROVISIONAL:** The specific ASR engine, LLM, TTS engine, framework, and
deployment topology are not selected. Once chosen, they are frozen as controlled
variables (Section 14).

---

## 12. Intervention Methodology

**The intervention is NOT predetermined.**

The scientific process is:

```
Baseline experiment
→ observe failures
→ classify failures
→ identify failure mechanisms
→ design targeted architecture
→ implement intervention
→ test intervention
→ compare results
```

The "Aether Veil" architecture **may** become the intervention, but it must be
derived from observed failure mechanisms. It must **not** be designed in advance
in order to produce a positive result.

**PROVISIONAL:** No intervention architecture has been designed, named in detail,
or implemented. Its specification is an output of the baseline experiment, not
an input to it.

---

## 13. Hypothesis

> Increasing Egyptian Arabic–English code-switching intensity will reduce
> speech-recognition accuracy and end-to-end task accuracy and may increase
> response latency. A targeted architecture designed around observed failure
> mechanisms will mitigate at least some of this degradation.

Note the asymmetry in strength embedded in the statement, as decided on Day 1:
the degradation of ASR accuracy and task accuracy is asserted, while the
latency effect is stated as "may increase". Test statistics, effect sizes,
significance thresholds, and the comparison procedure for baseline vs.
intervention are **not** specified here and are **PROVISIONAL**.

---

## 14. Controlled Variables

During the baseline experiment, the following are held constant:

- restaurant database
- task definitions
- expected answers
- system prompt
- model versions
- model settings
- ASR configuration
- tool definitions
- evaluation criteria
- audio conditions
- measurement procedure
- hardware/runtime environment where practical

**The principal experimental variable is code-switching intensity.**

Any change to a controlled variable after the baseline run invalidates direct
comparison with that run and must be recorded in the research decision log
before further trials are executed.

---

## 15. Experimental Boundaries

The research concerns Egyptian Arabic–English code-switching in **controlled
voice-agent business interactions**.

Findings must **not** be automatically generalized to:

- all Arabic speakers
- all Egyptian speakers
- all voice agents
- all languages
- all AI systems
- all commercial applications

Any claim in the final write-up must stay inside the boundary actually tested.

---

## Open Items Requiring Researcher Decision

Collected here so they are not lost between sessions:

| # | Item | Section |
|---|------|---------|
| 1 | Numerical boundaries for C1–C4 | 7 |
| 2 | Definition of the C0 sub-conditions | 7 |
| 3 | Tokenisation/annotation procedure for lexical-token counting | 6 |
| 4 | Trials per family and per condition; repetitions | 4, 5 |
| 5 | Reference transcript convention and WER normalisation | 9.1 |
| 6 | End-to-end task accuracy rubric | 9.2 |
| 7 | Latency instrumentation points and timing source | 9.3 |
| 8 | Error classification decision procedure and annotator | 10 |
| 9 | Concrete ASR / LLM / TTS technology selection | 11 |
| 10 | Restaurant database contents | 3 |
| 11 | Statistical analysis plan (tests, effect sizes, thresholds) | 13 |
| 12 | Matched-set construction and meaning-equivalence verification | 8 |
