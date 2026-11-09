# Egyptian Arabic–English Code-Switching in Voice Agents

A research project studying how increasing Egyptian Arabic–English code-switching
intensity affects the **latency**, **speech-recognition accuracy**, and
**end-to-end task accuracy** of a voice agent, and whether a **targeted
architecture** can mitigate the resulting degradation.

**Current state:** the research instrument is built and tested. The design
documents and the research decision log exist, and a working agent runs the full
pipeline against the fictional restaurant database. **No research data has been
collected and no trial has been executed** — the methodology still has open items
awaiting researcher approval, listed at the end of
`01_Research_Design/Day_01_Research_Design.md`.

---

## What this project is

The project runs a controlled experiment on a voice agent that serves a
**fictional Egyptian restaurant**. Users speak Egyptian Arabic, English, or
Egyptian Arabic–English code-switched speech. The agent must understand requests
and perform controlled business tasks against a fixed database.

The manipulated variable is **code-switching intensity**, measured as English
lexical-token proportion (English lexical tokens / total lexical tokens × 100).
Five conditions are intended, from C0 (0%) upward.

The scientific sequence is deliberately one-directional:

```
Baseline experiment → observe failures → classify failures
→ identify failure mechanisms → design targeted architecture
→ implement intervention → test intervention → compare results
```

The intervention is **not** predetermined. It is an output of the baseline
experiment, not an input to it.

---

## Folder structure

| Folder | Contents |
|--------|----------|
| `01_Research_Design/` | Research design documents, including `Day_01_Research_Design.md` (the authoritative Day 1 design record) |
| `02_Literature/` | Prior work, references, and positioning of this study |
| `03_Business_Environment/` | Design and contents of the fictional restaurant database and business rules |
| `04_Task_Dataset/` | Task definitions and the matched-task trial sets across code-switching conditions |
| `05_Audio/` | Audio assets and audio-condition specifications |
| `06_Baseline_Agent/` | The straightforward baseline voice-agent implementation |
| `07_Experiments/` | Experiment configuration, run orchestration, and raw run records |
| `08_Intervention/` | The targeted architecture, once it is derived from observed failure mechanisms |
| `09_Analysis/` | Scoring, statistics, and analysis of collected results |
| `10_Figures_Tables/` | Generated figures and tables for the write-up |
| `11_Paper/` | Manuscript text |
| `12_Research_Log/` | `Research_Decision_Log.md` — dated record of every scientific decision and change. Also `Pending_Decisions_For_Researcher.md`, which holds open questions raised during implementation for the researcher to decide |

Naming convention: numbered folders are ordered roughly by the workflow. Folders
`08_Intervention/` onward stay empty until the baseline experiment has been run
and its failures classified.

---

## Scientific decisions vs. implementation

This distinction governs how work in this repository is done.

**Scientific decisions** belong to the researcher. They include, non-exhaustively:

- the research question and its scope boundaries
- what is and is not an outcome variable
- the definition of code-switching intensity
- the numerical boundaries of the experimental conditions
- the definition of the experimental unit
- the matched-task principle and how meaning-equivalence is verified
- metric definitions (WER conventions, latency definition, task-accuracy rubric)
- the error classification scheme and how failures are attributed
- the controlled-variable list
- which technology implements each pipeline stage
- the contents of the restaurant database
- the statistical analysis plan
- when a result is considered to support or reject the hypothesis

**Implementation** belongs to the coding agent. It includes:

- creating and maintaining folders and file layouts
- writing code that realises an approved decision exactly
- scaffolding, configuration, orchestration, logging, and storage
- making the pipeline runnable and reproducible
- implementing an intervention *after* the researcher has specified the target
- refactoring and bug fixing that do not change approved decisions

**The rule: the coding agent implements approved science and does not alter it.**
The coding agent may not change, reinterpret, refine, or invent a scientific
decision. If implementation work reveals that a scientific decision needs to
change, the coding agent flags it and waits. It does not act on it.

### Items marked PROVISIONAL

`01_Research_Design/Day_01_Research_Design.md` marks unsettled items as
**PROVISIONAL**. A PROVISIONAL item is *not* an invitation to pick a sensible
value. Filling in a PROVISIONAL value is a scientific decision and requires
researcher approval. The day-1 list of items awaiting approval is at the end of
that document, under "Open Items Requiring Researcher Decision".

---

## How changes are recorded

Every scientific decision is logged in `12_Research_Log/Research_Decision_Log.md`
with its date and rationale. Methodological changes after Day 1 are recorded in
that log's "Future Methodological Changes" section, which is intentionally empty
until a change is actually approved.

The decision log is written by the researcher only. When implementation work
raises a question that needs a scientific answer, it goes into
`12_Research_Log/Pending_Decisions_For_Researcher.md` as a proposal and waits
there.

A change to any controlled variable after the baseline run requires a log entry
**before** further trials are executed, because it affects comparability with
the baseline data.

---

## The instrument

`src/nbva/` implements the baseline architecture from D1-11 as a single pipeline:

```
microphone → VAD → ASR → LLM (with tool calls) → business tools → TTS → speakers
                                        ↓
                                  JSONL trial log
```

| Area | Module | Notes |
|------|--------|-------|
| ASR | `nbva.asr` | `faster_whisper` (local, no key), `openai`, plus `manual` and `reference_echo` stubs for offline work |
| LLM | `nbva.llm` | `openai_compatible` (Ollama, vLLM, LM Studio), `openai`, `anthropic`, `google`, and a `scripted` offline stub |
| TTS | `nbva.tts` | `edge` (neural, no key, needs internet), `sapi`, `openai`, `null` |
| Business layer | `nbva.db`, `nbva.tools` | 14 tools over a fictional restaurant, priced deterministically |
| Session and pipeline | `nbva.agent` | trial lifecycle, state, tool loop, grounding checks |
| Logging | `nbva.obs` | one JSONL per session, with latency markers and participant attribution |
| Measure and analysis | `nbva.research` | the code-switching measure, task evaluation, error classification |

### Running it

```powershell
pip install -r requirements.txt

python -m pytest                      # the whole suite, offline, no keys
python -m nbva preflight              # would a run start? models, audio, task set
python -m nbva check                  # config, database, tools, measure, audio devices
python -m nbva audit                  # declared vs measured code-switching condition
python -m nbva devices                # list microphones and speakers
python -m nbva config --key llm.model # inspect a resolved setting
python -m nbva live                   # real-time microphone and speakers
python -m nbva chat                   # typed conversation, same pipeline, no audio
python -m nbva session --task-id DEV-ORD-01   # replay a trial from the task file
```

The package is not installed into the interpreter by default, so the commands
above assume either an editable install (`pip install -e .`) or
`$env:PYTHONPATH = "src"`.

### Before a run: preflight

`preflight` answers one question — would a session start and run a turn, or fail
on its first one — without opening a microphone or writing a session log. It
checks the LLM endpoint actually answers and has the configured model loaded, the
ASR model is installed and cached, TTS builds, audio devices open, and the task
set is condition-consistent. It exits non-zero if anything blocking is wrong, and
names each open scientific question as an advisory even when everything technical
passes.

`check` and `preflight` are not redundant. `check` proves the instrument is
internally coherent; `preflight` proves the machine is ready.

### Checking the task set: audit

`audit` measures every authored turn and compares it with the condition that turn
declares:

```powershell
python -m nbva audit                  # distribution and mismatches
python -m nbva audit --all            # list every turn, not just mismatches
python -m nbva audit --json           # machine-readable
python -m nbva audit --task-id DEV-CTX-01
```

The shipped development set is deliberately **inconsistent**: 16 of its 33 turns
measure into a different band than they declare, and the labels are systematically
low rather than scattered, which points at the hand-written labels rather than at
the measure. It exists to develop and test the instrument, and it is not the
research task set. `audit` makes that visible instead of leaving it to be
discovered during analysis.

### Exercising the live path without a human

Two development modes drive the real `LiveVoiceLoop` turn handler, which is
otherwise only reachable by opening a microphone:

```powershell
python -m nbva live --self-test                    # open the mic, confirm capture, close
python -m nbva live --audio-file clip.wav          # push a clip through the live handler
python -m nbva live --audio-file clip.wav --reference-text "عايز اتنين تشيكن برجر"
```

`--audio-file` runs the same handler a participant session does, so the
VAD-derived latency markers, ASR, the tool loop and TTS are all exercised.
`--reference-text` feeds a stub ASR its words, and becomes the expected output to
compare against with a real ASR. Both refuse to run as a research run, because a
recorded clip or a short capture produces a log that looks like participant
speech and would otherwise end up in the dataset.

### Configuration

Layered YAML in `config/`, overlaid by extra `--config` files, then `--set
key=value` overrides, then `NBVA_` environment variables. Every resolved value
and a fingerprint of the config are written into the session log, so a run can be
traced back to the settings that produced it.

Two settings are deliberately conservative:

- `experiment.is_research_run` defaults to `false`. A run marked as research
  requires a pseudonymous `experiment.participant_id` and refuses the offline
  LLM stub, because a stub has no model behind it and cannot produce valid data.
- `experiment.business_weekday` defaults to `auto`, which follows the calendar.
  **A formal run must pin it**, because promotions and opening hours depend on
  the day. Development tasks pin it per task. `preflight` refuses a research run
  that has not.

### Ground rules enforced in code

These are not conventions the agent is trusted to follow; the code refuses to
proceed:

- The LLM has no business facts of its own. Every price, item, fee, hour and
  promotion it states must come from tool output in the same conversation, and
  the response is checked against the numbers the tools returned.
- Unknown menu items, options and zones are refused with a candidate list
  instead of being guessed.
- Promotions are never stackable, are evaluated in dataset order, and a
  promotion gated on the customer stating something is withheld until the
  customer actually states it.
- A confirmed order cannot be edited, so a total already stated to a
  participant cannot be silently repriced.
- Orders keep the weekday they were priced under, so re-totalling later in a
  session cannot change a number the customer was already told.
- **A research run does not start on a task set whose turns do not measure into
  the condition they declare.** Code-switching intensity is the manipulated
  variable, so a mislabelled task set means the independent variable is not what
  the design says it is — and it is knowable before anyone speaks, so there is no
  reason to discover it during analysis. Development runs only warn, because an
  inconsistent set is normal while a set is being authored.

Two things this deliberately does **not** decide, because they are scientific
rather than mechanical:

- How many trials each condition needs, and whether an imbalance is acceptable.
  `audit` reports the distribution and stops.
- What to do with a participant whose speech drifts out of its authored
  condition. The turn is recorded with `condition_consistent: false` and **kept**,
  so the researcher can decide what it means. Discarding a trial is the
  researcher's call, not the instrument's.

### Data

`data/restaurant/nile_bites.json` is the fictional restaurant; see
`data/restaurant/README.md` for what it contains and what the engine reads from
it. `data/tasks/tasks.yaml` holds the **development** task set, which exists to
develop and test the instrument and is explicitly not the research task set.

Captured audio, logs and trial data are gitignored. Raw participant speech and
trial records do not belong in version control.

### Demonstration phone prototype

The existing pipeline can also run behind a local HTTP server suitable for a
mobile-phone demonstration. The server keeps a live `Session` per phone call,
restores recent caller dialogue from a local SQLite memory file, and exposes
the same grounded business tools used by the research instrument:

```powershell
$env:PYTHONPATH = "src"
python scripts/phone_server.py --set llm.provider=openai_compatible
```

The server loads credentials from `NBVA_ENV_FILE` or the user's private
`~/.env` file. The current local setup uses OpenRouter with a tool-capable
model selected outside the repository; the API key is never written to source
control or returned by the health endpoint.

For a local text demonstration, send JSON to `/api/chat`:

```powershell
curl.exe -X POST http://127.0.0.1:5000/api/chat `
  -H "Content-Type: application/json" `
  -d '{"call_id":"demo","caller_id":"demo","text":"عندكم برجر؟"}'
```

For a Twilio demonstration, expose the server through an HTTPS tunnel or
deploy it to a public HTTPS host, then set the phone number's Voice webhook to
`POST /twilio/voice`. Twilio posts speech transcripts to `POST /twilio/gather`;
the bridge returns TwiML that asks for the next utterance. Configure the LLM
endpoint and credentials through environment variables or a private config
override; secrets are never committed to this repository.

This bridge is a prototype integration boundary, not formal research
instrumentation. The fictional restaurant, synthetic/pilot material, and
development logs remain non-research data until the researcher approves the
open methodological decisions.

---

## Project boundaries

The research concerns Egyptian Arabic–English code-switching in controlled
voice-agent business interactions. Findings do not automatically generalize to all
Arabic speakers, all Egyptian speakers, all voice agents, all languages, all AI
systems, or all commercial applications. See Section 15 of the Day 1 design
document.
