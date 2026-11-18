# Experiment Protocol: Model Capability × Agent Architecture

Status: **SPECIFICATION — CANDIDATE DESIGN, NOT FROZEN. NO FORMAL DATA COLLECTED.**
This protocol operationalises `01_Research_Design/Current_Direction.md`. It
supersedes the archived C0–C4 protocol at
`01_Research_Design/_superseded/EXPERIMENT_PROTOCOL_v1_code_switching.md`.

## 1. Research question

How do agent architecture and language-model capability independently and
jointly affect task accuracy, response latency, and cost of Egyptian
Arabic–English conversational voice agents in tool-based tasks?

## 2. Independent variables (2 × 2)

| Factor | Level | Code |
|---|---|---|
| Model capability | weak/inexpensive model | M |
| Model capability | strong model | S |
| Architecture | conventional sequential baseline | A0 |
| Architecture | optimized reliability/efficiency architecture | A1 |

Cells: `M-A0`, `M-A1`, `S-A0`, `S-A1`. Model identities remain unapproved;
both must support the same tool interface and comparable settings.

## 3. Dependent variables

**Primary — task accuracy.** Assertion-based grading against deterministic
business state (order lines, quantities, totals, zone, status, required tools).
Wording is never graded.

**Secondary.** First-audio latency (`speech_end → first response sample`);
turn-completion latency (`speech_end → final response ready`); cost per
successful task where consistently measurable. The two latency endpoints are
recorded and analysed separately and are never mixed.

**Diagnostic.** Tool-selection accuracy, tool-argument accuracy, context
retention, grounding violations, clarification rate, failure category, token /
call overhead.

## 4. Controlled variables (frozen per experiment)

Recorded in `freeze.json`: ASR provider/model/settings; TTS provider/voice/
settings; task set and references; database snapshot + SHA-256; system prompt
copy + SHA-256; tool registry; model identifiers and provider settings;
temperature/top-p/max tokens; failure thresholds; Python and package versions;
pinned business weekday. Shipped `config/*.yaml` files are not edited for a run;
the freeze copies the resolved values.

## 5. Architectures

**A0 — conventional sequential baseline.** Full-utterance speech → full ASR →
single LLM tool loop → deterministic tools → complete TTS → playback.

**A1 — optimized reliability/efficiency architecture.** Five components from
`Current_Direction.md` §4: context manager; orchestration; structured tool
gateway; validation/reliability layer; latency path. A1 is evaluated as a
**combined** architecture. Component-level claims require ablations, and the
ablation switches must exist before such claims.

## 6. Trial structure

One trial = one task (possibly multi-turn) executed in one fresh session per
cell, with fresh business state. Heavy providers are shared to avoid re-loading,
but state never crosses trials. Trial ids are namespaced per cell
(`<task>-<model>-<architecture>`).

## 7. Metrics and records

Per-trial record: cell, model/architecture ids, task id/family, reference and
observed transcripts, task success/score, failure category (+detail), speech-end
and response timestamps, first-audio and completion latency, component spans,
tool calls, token usage, cost estimate, error.

CSV + JSONL exports use fixed columns; batch and live runs are stamped with
`audio_mode` and analysed separately.

## 8. Error categories

The ten locked categories in `research/failure.py` remain the taxonomy.
Auto-classification starts human coding; it does not replace it. Late trials are
recorded as failures with the metric that tripped recorded in `failure_detail`.

## 9. Logging and reproducibility

One JSONL line per record + flat CSV under the run directory. No secrets or
participant data in logs. A run is reproducible iff `freeze.json`, database
snapshot, prompt copy, task/trial files, audio, conditions snapshot, software
versions, and exact model identifiers are archived together. Hosted-model
nondeterminism is documented, not hidden.

## 10. Development vs formal research data

Development (allowed now): unit/acceptance tests with fakes, synthetic audio,
single-turn smoke runs labelled `dev-smoke`, pilot variability inspection.
Formal research data (NOT started): human-participant audio, frozen execution,
statistics reported as findings. `data/pilot/` is synthetic and must never be
cited as evidence.

## 11. Commands

```powershell
python scripts/run_experiment.py validate --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml
python scripts/run_experiment.py dist --trials data/pilot/trials.yaml
python scripts/run_experiment.py run --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml --out runs/<run-id>/
```

## 12. Open items requiring researcher approval

1. Choose the weak and strong models (with recorded selection criteria).
2. Freeze the final research question and title.
3. Fix trial counts and exclusions before formal collection.
4. Define first-audio and turn-completion failure thresholds (P-10).
5. Approve component ablation plan before making component-level claims.
6. Approve any human-participant collection (consent, protocol, storage).
7. Confirm the status of superseded C0–C4 material (secondary analysis vs
   historical only) — default: historical only.
