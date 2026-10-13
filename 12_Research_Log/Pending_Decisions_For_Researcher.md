# Pending decisions for the researcher

**This is not the decision log.** `Research_Decision_Log.md` states that the
coding agent may not write decisions into it. The entries below are therefore
*proposals* raised during implementation, waiting for the researcher to accept,
reject or rewrite them. Once accepted, they belong in `Research_Decision_Log.md`
as dated entries with the researcher's own rationale.

Each item says what the code does now, why, and what would change if the
researcher decides otherwise.

---

## P-01 — Latency is measured to the first audio sample, not the acoustic wave

**Code now.** The primary latency is the interval from detected end-of-user-speech
to the moment the first response audio sample is handed to the output stream.
Speaker playback happens afterwards and is not included.

**Why.** It is the only endpoint that is identical across TTS providers, and it
is the point where the agent has finished all of its own work. Waiting for the
wave to reach the speaker would fold playback buffer behaviour and device latency
into a linguistic measure.

**If changed.** If the study wants perceived latency, the acoustic endpoint has to
be captured per platform, which is not portable, and the measure stops being
comparable across machines. Recommendation: keep the sample endpoint as the
primary measure and treat the speaker endpoint as a separate diagnostic if
needed.

## P-02 — C1–C4 band boundaries are still provisional

**Code now.** C0 is fixed at 0%. C1–C4 read their boundaries from config and ship
with placeholder values, so the machinery works but the numbers are not
scientifically meaningful yet. `preflight` and `audit` both state this
explicitly rather than letting a green run imply the boundaries are settled.

**Blocked on the researcher.** The boundaries depend on the tokenisation and
annotation procedure, which is itself an open item. Nothing else can be finalised
first.

**New evidence for this decision.** `nbva audit` on the development task set
found 16 of 33 turns measuring into a different band than they declare, and the
errors run in both directions:

| declared | actually measured | count |
|---|---|---|
| C0 | C1 | 1 |
| C1 | C0 | 5 |
| C2 | C0 / C3 / C4 | 2 / 2 / 3 |
| C3 | C4 | 3 |

The dominant pattern is the C2 and C3 tasks measuring *higher* than they are
labelled, not lower. A one-band error in either direction, clustered at adjacent
boundaries, is the signature of a labelling error rather than of a noisy
measure: noise would scatter trials across the whole range, and these land
exclusively on neighbouring bands. That is evidence about the *labels*, and it is
evidence the boundaries are not the obvious culprit. It is not by itself
evidence for any particular new boundary values, which remain a judgement the
researcher has to make on their own merits.

## P-03 — WER normalisation convention

**Code now.** WER is computed with a full-matrix Levenshtein alignment over
word-level tokens, Arabic orthography is lightly normalised, Egyptian Arabic
numeral words are mapped to digits, and the English/Arabic language of each
token is tagged rather than guessed from script.

**Open.** Whether to strip Egyptian Arabic discourse particles, how to treat
English loans written in Arabic script, and whether code-switched tokens count as
English or as their own class. These change every WER number, so they must be
fixed before any trial.

## P-04 — Task-success rubric

**Code now.** A task passes when the final state of the deterministic layer
matches the pinned expectations: the order lines, the money, the zone, the
status, and the tools that were called. The state is the source of truth, not
what the agent claimed in words.

**Open.** Whether a task also requires the response to *state* the result
correctly, and how a partially correct order is scored. A strict state-only
rubric will not catch an agent that gets the order right and tells the customer
the wrong total.

## P-05 — Error annotation procedure

**Code now.** Failures are auto-classified into the ten Day 1 categories with a
confidence and a list of reasons, and a human can override the label.

**Open.** The annotator, the agreement procedure, and the treatment of failures
with several causes. D1-10 leaves all three open.

## P-06 — Statistical analysis plan

**Code now.** Nothing. No test, model or threshold has been chosen.

**Blocked on the researcher.** Trial counts and cell sizes come first, because
they determine which test is even appropriate.

## P-07 — Technology selection

**Code now.** Defaults are a local `faster-whisper` model, an OpenAI-compatible
LLM endpoint, and Edge TTS, all swappable by config. The model names in
`config/` are placeholders, not a decision.

**Open.** D1-11 leaves the specific technologies open, and they are controlled
variables under D1-14, so they must be frozen and recorded before the baseline
run.

## P-08 — What happens to a trial whose measured condition drifts from its authored one

**Code now.** Every turn records `declared_condition`, `measured_condition`, the
English percentage, and `condition_consistent`. A drifted turn is **kept**, not
dropped, and the flag is written to the session log. Separately, a research run
refuses to *start* if the authored task set contains mismatches, because that is
knowable before anyone speaks.

**Open.** Whether a drifted trial counts toward the analysis, is excluded, or is
analysed as a separate stratum. This is a scientific decision and the instrument
deliberately does not make it: dropping a trial on the agent's own authority would
remove participant data and quietly change the design, which is worse than
carrying a known-defective trial into the analysis where it is visible.

**Needed from the researcher.** An exclusion rule stated in advance, ideally with
a tolerance. The current tolerance is 2.0 percentage points, which is a
placeholder and is not defensible as a final rule.

## P-09 — How many trials each condition needs, and whether imbalance is acceptable

**Code now.** `nbva audit` reports the per-condition distribution and names
conditions that are unbalanced by 2x or more, and conditions with no trials at
all. It reports and stops.

**Open.** The actual trial counts, the cell sizes, and whether an imbalance is
acceptable for the intended test. This is downstream of P-02: the boundaries
determine how many distinct trials a condition needs.

**Note on the current numbers.** The development set is heavily skewed toward the
extremes — 14 turns measure into C0 and only 2 into C2. That is acceptable for a
development set, whose purpose is to exercise every code path. It would not be
acceptable for a research set, and the audit is what makes the difference visible
before anyone is exposed to a trial.

---

## Implementation notes that are not decisions

These were resolved while building, and are recorded here only so the reasoning
is not lost. None of them is a scientific choice, and none needs a log entry.

- **Trial ids are unique per presentation.** `trial_id` is
  `<task_id>-<nnn>` rather than the bare task id, so showing the same task twice
  does not collide. `task_id` is still logged separately, so joining records to
  tasks is unaffected.
- **A confirmed order cannot be edited.** Editing one would reprice a total the
  participant was already told, with no acknowledgement from them. The agent is
  told to place a new order instead. The fictional restaurant is the controlled
  environment, so this is a dataset rule rather than a claim about real ones; the
  researcher may prefer the looser rule.
- **Gated promotions fail closed.** A promotion with a `requires_mention`
  requirement that has no implementation never applies, so a missing check can
  never grant a discount by accident.
- **The offline LLM stub is refused for research runs.** `llm.provider:
  scripted` raises if `experiment.is_research_run` is true. A stub has no model
  behind it, so nothing it produces is data.
- **The development task set covers every weekday promotion.** All other
  development tasks pin Wednesday, where no weekday promotion fires. Without the
  extra tasks, the weekday machinery would never be exercised by a trial. The
  student gate in particular is covered both with and without a student id, so a
  gate that was ignored would still fail the suite.
- **A research run must pin the business weekday.** `experiment.business_weekday:
  auto` follows the calendar, and a run whose weekday is not pinned can have its
  expected totals invalidated by a promotion firing on the day it happens to run.
  This is an implementation guard, but it enforces a scientific requirement: the
  weekday is a controlled variable under D1-14.
- **The live development modes cannot run as a research run.** `nbva live
  --audio-file` and `--self-test` refuse when `experiment.is_research_run` is
  true. Both write a session log that is indistinguishable from participant
  speech, so without the guard a smoke test could end up in the dataset.
- **A drifted trial is flagged, never silently dropped.** See P-08. The
  implementation deliberately stops at recording the disagreement.
