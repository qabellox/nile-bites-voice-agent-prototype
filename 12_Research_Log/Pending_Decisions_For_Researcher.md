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

## P-10 — Latency-failure threshold: the config value is dead and the effective value is 120s

**Code now.** Every runtime path uses **120 seconds**: `ExperimentRunner`
defaults (`runner.py:123-124`) feed `classify_failure` (`runner.py:394-396`),
`freeze_experiment` stamps 120 into `freeze.json` (`experiment.py:116,171-172` —
`run_experiment.py` calls it with no threshold arguments), the tests encode 120
(`test_experiment.py:195`), and EXPERIMENT_PROTOCOL §15.3 states 120 as the
default. Meanwhile `config/research.yaml:65` sets
`latency_failure_seconds: 10.0` and `:63` sets `asr_failure_wer_threshold: 0.5` —
and **no code ever reads `research.evaluation.*`** (verified: the only consumed
`research.*` keys are `research.codeswitch.*` and `research.wer.*`). The 10.0 in
the config file therefore has no effect today. It is a documentation trap: an
edit there would silently change nothing while `freeze.json` keeps freezing 120.

**Why it matters.** The two numbers classify the same trial differently — a 30s
turn is a latency failure under one and not under the other, which changes
`failure_category`, the failure distribution, and the robustness curve. Whatever
value is chosen, a formal run must not carry a config file that advertises a
different one than the behaviour it froze.

**Needed from the researcher (before any formal run; not decided here).**

1. Decide what qualifies as a failed or timed-out trial for this experimental
   setup. Consider what the threshold is actually about: the agent's own
   pipeline completing at all, versus participant-perceived responsiveness —
   and how streaming is judged against it, since streaming can hand over first
   audio early yet finish the whole turn late (time-to-first-audio and
   total-completion may need to be judged separately).
2. Justify the chosen value for this setup. Both 10 and 120 are currently
   unjustified placeholders; neither should win by appearing in a file.
3. Specify how timed-out trials enter the analysis: counted as failures in the
   denominator, carried only as a failure category, or excluded — stated in
   advance, like P-08's drift rule.
4. Once decided, the effective number must be read from config into the runner
   *and* stamped by `freeze_experiment` from that same source, so config,
   `freeze.json` and behaviour cannot disagree again. That wiring is an
   implementation step and will be done only after the decision. The same fix
   applies to the dead `asr_failure_wer_threshold` key (currently effective
   0.5 everywhere by hard-coded default).

### P-10 implementation proposal (agent; gated on the decisions above)

No cutoff is chosen here. This is the wiring that will be built once the
researcher has defined the two concepts and their thresholds.

**Two metrics, named distinctly everywhere.**

- **First-audio latency** — speech end → first response audio sample. Already
  the LOCKED primary metric (`latency_s`; `LatencyMarkers.response_audio_start`).
- **Turn completion** — speech end → the response and its required processing
  are finished (final audio ready; final tool loop and synthesis done). This
  metric does not exist yet: `TrialRecord` has no completion field, and batch
  records stamp `speech_end_utc`/`response_audio_utc` with the record-creation
  time (`runner.py:417`) — placeholders, not measured endpoints. The live path
  already stamps the underlying marker (`response_audio_end_mono`,
  `events.py:78,148`); it is never surfaced into records.

**One authoritative source.** A single function — `thresholds_from_config(config)`
— reads both cutoffs from `research.evaluation` (key names to follow the
researcher's definitions, e.g. `first_audio_failure_seconds`,
`turn_completion_failure_seconds`). It becomes the only reader:

- `ExperimentRunner` takes the values as a constructor argument from that
  function; the `getattr(..., 120.0)` fallbacks (`runner.py:123-124,394-396`)
  are removed so no hard-coded number can survive.
- `freeze_experiment` calls the same function and stamps both values into
  `freeze.json.thresholds`, so config, freeze and behaviour share one code path
  and cannot drift apart.
- The dead keys are replaced, and a regression test asserts that editing
  `research.yaml` changes both the runner's applied value and the frozen value —
  the test whose absence allowed the 10-vs-120 state to exist.

**Applied identically to both architectures.** Classification already runs at
one shared point for both arms (`runner.py:392-403`, inside the
baseline/streaming loop). Both arms must supply both metrics to it:

- First-audio: the arms do not currently compute the same construct — baseline
  sums component spans and omits `tools_ms` (`runner.py:380`), streaming uses
  `first_audio − endpoint` (`runner.py:384-387`). Standardise to one definition
  (first response audio available, measured from speech end) computed the same
  way for each arm, or document arm-specific proxies in `freeze.json` so they
  are never compared as if identical.
- Completion: both arms must emit a completion endpoint. Baseline's completion
  coincides with its first audio (sequential: full TTS before playback);
  streaming's is the final chunk — surfaced into `TrialRecord.turn_completion_s`
  and the CSV columns (`experiment.py:405`).
- `classify_failure` receives both durations and both thresholds and records
  **which** metric tripped in `failure_detail` (e.g. `latency_metric:
  first_audio | turn_completion`). The ten locked categories are unchanged —
  `latency/timeout` stays one category, the detail keeps the two outcomes
  distinguishable so a single cutoff cannot silently merge them.

**Observed behaviour the researcher should factor into the analysis decision:**
`classify_failure` returns immediately when the task succeeded
(`failure.py:119-120`), so today a trial can exceed the threshold and keep
category "ok". Whether exceeding a latency threshold should mark an otherwise
successful trial as a latency failure — or latency should enter analysis only as
the raw recorded metric — is part of decision 3 above.

**Not started until P-10 is decided:** the cutoffs themselves, the definition of
completion in each `audio_mode` (batch records are audio-READY, never played,
per protocol §9, and are never mixed with live endpoints), and how each metric
enters the analysis. After the decision, the wiring above lands as one
implementation commit with tests.

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
