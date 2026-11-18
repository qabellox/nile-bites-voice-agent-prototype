# Experiment Protocol: Baseline vs Streaming Intervention

Status: **INFRASTRUCTURE READY — NO FORMAL DATA COLLECTED.**
This document specifies the controlled comparison. Nothing here starts data
collection; the pilot dataset under `data/pilot/` is synthetic development
material and is never to be represented as research data.

## 1. Research question (unchanged)

How do latency, ASR accuracy, end-to-end task accuracy, and robustness under
increasing Egyptian Arabic–English code-switching differ between the
conventional sequential voice-agent architecture and the streaming
incremental architecture?

## 2. Independent variable

Architecture, two levels: `baseline` (sequential: full-utterance ASR → LLM →
tools → complete TTS → playback) vs `streaming` (chunked ASR →
partial/committed/final hypotheses → incremental understanding → speculative
read-only tools → streaming LLM → streaming TTS).

## 3. Dependent variables (per trial, per architecture)

- Task accuracy: `task_success` + `task_score` from assertion-based grading
  against deterministic business state (wording never graded).
- ASR accuracy: WER of ASR transcript vs verified reference transcript.
- Latency: `speech_end → response_audio` in seconds, plus component spans
  (`asr_ms`, `llm_ms`, `tools_ms`, `tts_ms`).
- Robustness curve: the above as a function of code-switching condition.
- Failure category: one of the ten locked categories (`failure.py`).

## 4. Controlled variables (frozen per experiment)

Recorded verbatim in `freeze.json` by `research.experiment.freeze_experiment`:
ASR provider/model/settings, LLM provider/model/settings, TTS
provider/voice/settings, beam setting (default greedy; beam-5 stays opt-in),
database snapshot (file copy + sha256), system prompt (copy + sha256),
failure thresholds, condition-band snapshot, software versions
(python + key packages). The shipped `config/*.yaml` files are never edited
for an experiment; the freeze copies values instead.

## 5. The locked measure (unchanged, non-negotiable)

English lexical tokens / total lexical tokens × 100, computed from the
VERIFIED REFERENCE transcript, never from ASR output. C0–C4 numeric band
boundaries are PROVISIONAL (`config/conditions.yaml`) and are frozen by the
researcher only AFTER inspecting the pilot distribution
(`pilot_distribution()` reports per-band n/min/max/mean and never chooses
boundaries). C0 (exactly 0%) is fixed by definition.

## 6. Baseline (research control)

`BaselinePipeline.process_turn`: full utterance → full ASR → tool-loop LLM →
grounding → complete TTS. Frozen; only already-approved bug fixes apply.

## 7. Intervention (candidate)

`nbva.stream.StreamingTurnLoop`: overlapping ASR windows, local-agreement
commits with counted revisions, per-commit language awareness (mixed content
preserved, never forced to one script), commit-gated speculative READ-ONLY
tools (allowlist enforced in code; `create/modify/cancel/confirm_order`
require final interpretation), token-streaming LLM, sentence-streaming TTS,
barge-in hook. Single-pass mixed decoding first; dual-decode only if fidelity
measurements demonstrate single-pass inadequacy.

## 8. Trial structure

One trial = one `TrialSpec` (task + frozen audio file(s) + speaker metadata
where permitted + verified English %) × both architectures. Multi-turn tasks
run all turns in one session per architecture with fresh business state per
trial (fresh `Session` ⇒ fresh order store + tool registry; heavy providers
shared). Trial IDs are namespaced per architecture (`<trial>-baseline`,
`<trial>-streaming`).

## 9. Metrics (per `TrialRecord`)

`architecture, trial_id, task_id, task_family, declared/measured_condition,
condition_consistent, verified/measured_english_pct, reference_transcript,
asr_transcript, wer, task_success, task_score, failure_category (+detail),
speech_end_utc, response_audio_utc, audio_mode, latency_s, asr/llm/tools/tts
ms, tool_calls, per-turn detail, error`. Batch file runs stamp
`audio_mode=batch-synth` (audio READY, not played); live runs stamp
first-sample-to-device. The two endpoints are never mixed in analysis.

## 10. Error categories

The ten locked categories in `research/failure.py`, auto-classified by
`classify_failure` (conservative; `other` + human override when evidence is
insufficient). Auto-coding starts human coding; it does not replace it.

## 11. Logging format

- Research turns: existing session log (unchanged).
- Experiment output: one JSONL line per record (`write_jsonl`) + flat CSV
  (`records_to_csv`, fixed columns) under an experiment run directory.
- Streaming diagnostics: per-turn dev JSONL trace (`stream/trace.py`) with
  the full §11 event set; development only, never the dataset.
- No API keys or secrets are ever written to any log (names/versions only).

## 12. Reproducibility requirements

A run is reproducible iff: `freeze.json` + database snapshot + prompts copy +
`trials.yaml` + `tasks.yaml` + audio files + `conditions.yaml` snapshot are
archived together. Rerunning the same frozen inputs must reproduce the same
records up to hosted-model nondeterminism (documented, not hidden).

## 13. Development tests vs formal research data

Development (allowed now): unit/acceptance tests with fakes, synthetic
Edge-TTS controls, single-turn smoke runs labeled `dev-smoke`, pilot
distribution inspection. Formal research data (NOT started): human-participant
audio, frozen pilot execution, any statistics reported as findings. The
`data/pilot/` directory is synthetic by construction (see its README) and
must never be cited as evidence.

## 14. Commands (`scripts/run_experiment.py`)

Validate a dataset:
`python scripts/run_experiment.py validate --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml`
Show the verified-percentage distribution (boundary evidence, chooses nothing):
`python scripts/run_experiment.py dist --trials data/pilot/trials.yaml`
Run the experiment (freezes config, re-validates, writes JSONL + CSV):
`python scripts/run_experiment.py run --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml --out runs/<run-id>/ [--only-trial <id>] [--llm-model <model>] [--asr-language <code>]`
Overrides such as `--llm-model` exist only because the shipped default model
is not hosted at the dev endpoint; every override is recorded in
`freeze.json`, so runs stay reproducible and auditable.

## 15. Open items requiring researcher approval

1. Freezing C0–C4 numeric boundaries after pilot-distribution inspection.
2. Beam-5 vs greedy as a formal factor (currently dev-only toggle).
3. Latency-failure threshold (`latency_failure_seconds`, default 120s) and
   ASR-failure WER threshold (default 0.5) for `classify_failure`.
4. Any human-participant collection (consent, protocol, storage).
5. Full pilot execution (currently: infrastructure + one smoke trial only).
