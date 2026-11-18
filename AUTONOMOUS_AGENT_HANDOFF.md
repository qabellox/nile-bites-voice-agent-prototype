# Nile Bites Voice Agent — Autonomous Agent Handoff Blueprint

This document is the operational memory and master prompt for an AI coding agent
continuing the Nile Bites Egyptian Arabic–English voice-agent project. It is
intentionally self-contained, but the repository files remain the source of
truth. Do not treat this document as permission to change scientific decisions.

## 1. Mission

Build and evaluate a reliable, industrial-quality voice customer-service agent
for the fictional restaurant **Nile Bites**. The production demonstration should
support Egyptian Arabic, English, and code-switched speech; answer grounded
restaurant questions; preserve exact order items and quantities; speak through
ElevenLabs; and fail safely rather than guess.

The research project studies how Egyptian Arabic–English code-switching affects:

- latency;
- speech-recognition accuracy;
- end-to-end task accuracy; and
- robustness of a voice agent.

The project also provides a controlled baseline and a later targeted streaming
intervention. **No formal human-participant research data has been collected.**
Synthetic pilot material is development material and must never be reported as
research findings.

## 2. Absolute operating rules for a future AI agent

1. Inspect the repository and current Git status before editing.
2. Never expose, print, commit, copy, or place credentials in source, Markdown,
   tests, logs, screenshots, browser pages, or issue comments.
3. Secret names are safe to document; secret values are not.
4. Never revoke or replace a credential without a replacement and a verified
   rollback path.
5. Treat every wrong item, quantity, price, delivery fee, or order mutation as a
   safety defect.
6. Never allow an LLM response to create an order directly. The database and
   validated business tools are authoritative.
7. Never silently default a missing quantity to one. Ask for the quantity.
8. Never map “chicken sandwich” to “chicken burger,” or any other approximate
   product. Ask which exact catalog item is intended.
9. Never invent a price, total, ingredient, availability, promotion, fee, ETA,
   or opening hour. Retrieve it from the restaurant database/tools.
10. Preserve the customer’s exact item, quantity, constraints, and location.
11. Keep ordinary questions conversational and model-generated; do not replace
    the whole agent with canned templates merely to hide provider failures.
12. If the intelligence provider fails, fail closed with an explicit service
    error and do not create or alter an order.
13. Separate research changes from production/demo changes. Do not alter the
    scientific design without researcher approval.
14. Run focused tests after every behavioral change, then deploy only after
    production acceptance tests pass.
15. Use ASCII by default in code, but preserve existing Arabic data and text.

## 3. Physical workspace and partitions

Active repository:

```text
C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research
```

Windows volumes observed during the handoff:

```text
C:\
D:\
F:\
G:\
```

The project is currently on `C:`. The other volumes were visible but no project
copy was selected from them. Do not search, delete, mount, format, or relocate
any partition without explicit user authorization. The old notebook-only
project at `C:\Users\Qebaa\EgyVoice-Agent` was a different project and was
deleted earlier; do not recreate or use it.

Git remote:

```text
https://github.com/qabellox/nile-bites-voice-agent-prototype.git
```

Production URL:

```text
https://nile-bites-voice-agent-prototype-ui.vercel.app
```

## 4. Directory map

| Path | Responsibility |
|---|---|
| `api/index.py` | Vercel Python entrypoint; loads production config and creates the Flask app |
| `src/nbva/phone.py` | Production phone bridge, HTTP routes, memory, deterministic safety path, TTS endpoint |
| `src/nbva/agent/` | Session, pipeline, prompts, tool-loop integration |
| `src/nbva/llm/` | LLM provider adapters and OpenAI-compatible client |
| `src/nbva/asr/` | Local/hosted ASR adapters used by the research pipeline |
| `src/nbva/tts/` | Research TTS adapters |
| `src/nbva/db/` | Restaurant repository and order storage |
| `src/nbva/tools/` | Menu, delivery, order, totals, modification, and confirmation tools |
| `src/nbva/research/` | Measures, task scoring, failure classification, and experiment support |
| `src/nbva/stream/` | Streaming intervention architecture and diagnostics |
| `data/restaurant/nile_bites.json` | Fictional menu, aliases, ingredients, prices, zones, and business rules |
| `config/llm.yaml` | Research/default LLM configuration |
| `config/asr.yaml` | Research/default ASR configuration |
| `config/tts.yaml` | Research/default TTS configuration |
| `config/prompts/baseline_system_prompt.md` | Grounding and customer-service rules |
| `config/conditions.yaml` | Code-switching condition bands; provisional until approved |
| `tests/` | Unit, integration, pipeline, streaming, provider, and phone tests |
| `ui.html` | Production browser voice UI |
| `scripts/phone_server.py` | Local phone server |
| `scripts/dev_ui.py` | Local development UI |
| `scripts/run_experiment.py` | Research experiment validation, distribution, and runs |
| `01_Research_Design/` | Research design and Day 1 authoritative record |
| `02_Literature/` | Literature and study positioning |
| `03_Business_Environment/` | Fictional restaurant/business environment |
| `04_Task_Dataset/` | Matched tasks and trial specifications |
| `05_Audio/` | Audio assets and condition specifications |
| `06_Baseline_Agent/` | Baseline architecture documentation |
| `07_Experiments/` | Experiment orchestration and run records |
| `08_Intervention/` | Targeted intervention documentation |
| `09_Analysis/` | Scoring, statistics, and analysis |
| `10_Figures_Tables/` | Generated figures and tables |
| `11_Paper/` | Manuscript |
| `12_Research_Log/` | Research decision log and pending decisions |
| `runs/` | Experiment outputs and freeze manifests |
| `logs/` | Development/session logs; never place secrets here |

## 5. Runtime architecture

### Production browser path

```text
Microphone
  -> browser SpeechRecognition, language ar-EG
  -> transcript POST /api/chat
  -> mandatory OpenRouter LLM reasoning pass
  -> deterministic restaurant/order validation OR LLM tool loop
  -> grounded response text
  -> POST /api/tts
  -> ElevenLabs audio/mpeg
  -> browser playback
```

The current browser ASR is not ElevenLabs Scribe. It is browser-provided
`SpeechRecognition`/`webkitSpeechRecognition`; it can misrecognize quantities
before the server receives text. The UI requests multiple alternatives and
prefers an alternative containing a number, but this is not equivalent to a
dedicated server-side Arabic ASR system.

### Research baseline path

```text
microphone/audio -> VAD -> ASR -> LLM tool loop -> business tools -> TTS -> playback
                                      |
                                      +-> JSONL trial log
```

### Public endpoints

- `GET /healthz`
- `POST /api/chat`
- `POST /api/tts`
- `POST /twilio/voice`
- `POST /twilio/gather`
- `POST /twilio/hangup`

## 6. Current provider configuration

### Intelligence/comprehension

- Endpoint: OpenRouter OpenAI-compatible API.
- Environment variable for endpoint: `OPENAI_BASE_URL`.
- Credential variable: hidden Vercel Production `OPENAI_API_KEY`.
- Current production model: `nvidia/nemotron-3-ultra-550b-a55b:free`.
- Source fallback: same model in `api/index.py`.
- Previous paid choice `openai/gpt-4.1-mini` returned HTTP 402 because the
  OpenRouter account/key had no usable credit at the time.
- The mandatory reasoning pass calls the configured model for every text turn.
- The model must not be trusted to mutate orders without database/tool
  validation.

### Text-to-speech

- Provider: ElevenLabs.
- Credential variable: hidden Vercel Production `ELEVENLABS_API_KEY`.
- Voice variable: `ELEVENLABS_VOICE_ID`.
- Current voice ID: Sarah (`EXAVITQu4vr4xnSDxMaL`).
- Current model: `eleven_flash_v2_5`, configured by `ELEVENLABS_TTS_MODEL` or
  the code default in `src/nbva/phone.py`.
- The funded account was verified to return `audio/mpeg`.
- TTS does not perform speech recognition or comprehension.

### Speech-to-text

- Production browser UI: browser `ar-EG` SpeechRecognition.
- No provider API key controls this browser transcription.
- Research adapters include local faster-whisper, OpenAI-hosted Whisper, manual,
  and reference-echo stubs, depending on configuration.
- A future industrial upgrade should use server-side Arabic ASR with confidence,
  n-best hypotheses, timestamps, and a domain-aware correction/confirmation
  layer. Provision that service separately; do not pretend the LLM key is ASR.

## 7. Data and order safety contract

The source of truth is `data/restaurant/nile_bites.json`. The current catalog
includes five burgers, wraps/sandwich-like items, grills, breakfast, sides,
salads, drinks, delivery zones, prices, aliases, allergens, and business rules.

The order path must enforce:

- exact catalog matching;
- explicit quantity;
- explicit delivery zone when delivery requires it;
- database-calculated totals;
- no invented products;
- no silent quantity defaults;
- no order mutation from free-form LLM prose;
- confirmation before final confirmation action.

Examples:

- “three chicken sandwiches” is ambiguous and must ask which exact sandwich.
- “three chicken burgers” matches the catalog’s chicken burger and quantity three.
- “a chicken burger” has no explicit numeric quantity in this safety-critical
  interface and must ask for the quantity rather than assume one.
- “two burgers and five fries” must preserve both quantities and ask for burger
  type if “burger” is generic.

## 8. Production failure history and lessons

- Browser Arabic ASR has confused quantities while preserving product names.
- LLM tool calling on free models has been unreliable; critical supported order
  paths therefore use validated deterministic parsing and tools.
- Paid OpenRouter models can return HTTP 402 when credits are unavailable.
- Free OpenRouter models can return HTTP 429 or low-quality tool behavior.
- ElevenLabs previously returned `quota_exceeded`; adding credits restored TTS.
- Arabic digits/numbers are normalized and response prices/totals are rendered in
  Egyptian Arabic words for better speech.
- Generic canned responses were previously overused; ordinary questions must
  reach the model/tool loop.
- When a provider fails, the system must be explicit and fail closed, not return
  a success-shaped guessed answer.

## 9. Research integrity rules

Authoritative documents:

- `01_Research_Design/Day_01_Research_Design.md`
- `EXPERIMENT_PROTOCOL.md`
- `12_Research_Log/Research_Decision_Log.md`
- `12_Research_Log/Pending_Decisions_For_Researcher.md`

Do not change these scientific decisions autonomously:

- research question and hypotheses;
- code-switching definition;
- condition boundaries;
- experimental unit;
- matched-task equivalence;
- WER, latency, and task-accuracy definitions;
- controlled variables;
- failure categories;
- database contents;
- statistical analysis plan;
- whether a result supports a hypothesis.

The locked code-switching measure is:

```text
English lexical tokens / total lexical tokens * 100
```

It is computed from the verified reference transcript, not the ASR output.
C0–C4 boundaries are provisional until the researcher approves them after pilot
distribution inspection.

Formal data collection has not started. Do not call pilot data findings.
Human-participant collection requires consent, approved storage, and researcher
approval.

## 10. Reproducible research workflow

Before any formal run, freeze and archive together:

- `freeze.json`;
- database snapshot and SHA-256;
- prompt copy and SHA-256;
- `trials.yaml`;
- `tasks.yaml`;
- audio files;
- conditions snapshot;
- software versions and provider/model settings.

Useful commands:

```powershell
cd "C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research"
pip install -e .
python -m pytest
python -m nbva preflight
python -m nbva check
python -m nbva audit
python -m nbva devices
python -m nbva live
python -m nbva chat
python -m nbva session --task-id DEV-ORD-01
python scripts/run_experiment.py validate --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml
python scripts/run_experiment.py dist --trials data/pilot/trials.yaml
```

Development tests may use fakes and synthetic audio. Formal research runs must
be labeled, frozen, reproducible, and separated from development smoke tests.

## 11. Industrialization roadmap

### P0 — safety and observability

1. Replace browser-only ASR with server-side Arabic ASR or add a server audio
   upload path with n-best transcripts and confidence.
2. Add a structured intent schema:
   `intent`, `items`, `quantity`, `zone`, `confidence`, `clarification_required`.
3. Validate every LLM-produced field against the catalog and transcript.
4. Add an order preview/confirmation state before mutation.
5. Add idempotency keys and durable order state for retries.
6. Redact secrets and personal data from logs.
7. Add provider health, latency, quota, 402/401/429 metrics and alerts.

### P1 — conversation quality

1. Use the LLM for natural language understanding and response planning, not
   unvalidated arithmetic or catalog invention.
2. Keep responses concise, Egyptian Arabic when appropriate, and specific.
3. Preserve mixed-language input without forcing Urdu/Hindi-like pronunciation.
4. Add adversarial tests for wrong quantities, unsupported products, corrections,
   “remove,” “replace,” “add,” and multi-turn references.
5. Add a fresh-conversation control that clears local browser call state.

### P2 — production reliability

1. Use a paid, budget-capped reasoning model only after OpenRouter credit is
   confirmed; keep a tested fallback policy.
2. Use ElevenLabs Flash for real-time speech and monitor character quota.
3. Add Twilio call-state persistence and webhook authentication.
4. Add timeouts, circuit breakers, bounded retries, and clear degraded modes.
5. Add deployment smoke tests and rollback instructions.

### P3 — scientific productization

1. Keep baseline and intervention implementations isolated.
2. Freeze every formal run.
3. Pre-register analysis decisions before inspecting outcome data.
4. Report uncertainty, exclusions, failure categories, and provider versions.
5. Never mix live product telemetry with formal research data.

## 12. Copy-paste master prompt for another AI agent

Use the following as a system/developer prompt for an agent operating in this
repository:

```text
You are the senior engineering and research-integrity agent for the Nile Bites
Egyptian Arabic–English voice-agent project.

Workspace:
C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research

Repository:
https://github.com/qabellox/nile-bites-voice-agent-prototype.git

Production:
https://nile-bites-voice-agent-prototype-ui.vercel.app

Mission:
Make the voice agent reliable, natural, grounded, low-latency, and suitable for
an industrial demonstration while preserving the scientific validity of the
research project.

Before acting:
1. Read README.md, EXPERIMENT_PROTOCOL.md, the relevant Day 1 design record,
   the research decision log, and this AUTONOMOUS_AGENT_HANDOFF.md.
2. Run git status and inspect the relevant code path.
3. Identify whether the requested change is production behavior, research
   instrumentation, or a scientific decision. Never make scientific decisions
   yourself.

Architecture:
Browser microphone -> browser ar-EG SpeechRecognition -> /api/chat ->
mandatory OpenRouter reasoning pass -> validated database/tools or LLM tool loop
-> grounded response -> /api/tts -> ElevenLabs Flash Arabic audio.

Current providers:
- OpenRouter-compatible endpoint via OPENAI_BASE_URL.
- Hidden credential name: OPENAI_API_KEY.
- Current production model: nvidia/nemotron-3-ultra-550b-a55b:free.
- ElevenLabs hidden credential name: ELEVENLABS_API_KEY.
- ElevenLabs model: eleven_flash_v2_5.
- ElevenLabs voice ID: EXAVITQu4vr4xnSDxMaL.
- Browser ASR is not controlled by an API key.

Non-negotiable safety:
- Never print or commit secret values.
- Never invent a product, price, quantity, total, fee, ETA, or promotion.
- Never let LLM prose directly mutate an order.
- Never default a missing quantity to one.
- Never map a sandwich to a burger or make an approximate catalog match.
- If an item or quantity is ambiguous, repeat what was detected and ask one
  precise clarification question.
- If the reasoning provider fails, fail closed and do not mutate an order.
- Validate all state changes through the database-backed tools.

Conversation quality:
- Ordinary questions should reach the actual model/tool loop, not canned replies.
- Respond in the customer’s Egyptian Arabic, English, or natural mixture.
- Be formal and specific without repetitive greetings.
- Use tool results for facts and preserve the customer’s exact constraints.

Research integrity:
- Formal data collection has not started.
- Never call synthetic pilot data research findings.
- Do not alter code-switching definitions, condition boundaries, metrics,
  controlled variables, task equivalence, or analysis plans without researcher
  approval and a dated decision-log entry.
- Archive freeze.json, database/prompt/task/audio/condition snapshots and versions
  for formal runs.

Implementation method:
1. Reproduce the issue with a focused test or live request.
2. Find the producer of the wrong behavior, not merely a downstream symptom.
3. Make the smallest complete change using existing patterns.
4. Add a regression test for the exact failure.
5. Run the focused suite, syntax/type checks, and relevant production smoke tests.
6. Inspect logs for provider status, latency, tool calls, and error codes.
7. Deploy only after verification.
8. Report files changed, tests, deployment URL, remaining uncertainty, and any
   required user-side action.

If requirements conflict, prioritize:
1. safety/no hallucinated order mutations;
2. factual database grounding;
3. explicit provider errors;
4. scientific reproducibility;
5. natural conversational quality;
6. latency and cost.
```

## 13. Handoff checklist

- [ ] Read this document and the authoritative research documents.
- [ ] Confirm the active path is the `C:` repository above.
- [ ] Confirm no secret values are in Git or logs.
- [ ] Confirm `/healthz`, `/api/chat`, and `/api/tts`.
- [ ] Run `python -m pytest`.
- [ ] Test explicit quantities: one, two, three, Arabic-Indic digits, and English.
- [ ] Test missing quantities: must ask, never assume.
- [ ] Test unsupported “chicken sandwich”: must not become burger.
- [ ] Test exact chicken burger: must preserve quantity.
- [ ] Test multi-item order: every line and quantity must be preserved.
- [ ] Test price/total grounding against `nile_bites.json`.
- [ ] Check OpenRouter status and quota without exposing the key.
- [ ] Check ElevenLabs status and quota without exposing the key.
- [ ] Record any scientific question in `Pending_Decisions_For_Researcher.md`.
