# Nile Bites — Research, Engineering, and Business Handoff

**Prepared:** 18 November 2026
**Purpose:** Give a future AI coding/research-development agent the exact
project vision, verified technical context, scientific boundaries, business
direction, and operating protocol.
This is a handoff and working blueprint. The repository and approved research
records remain authoritative. Provisional ideas in this file are not approved
scientific decisions.

## 1. Role, ownership, and mission

The researcher is a 16-year-old student in Port Said, Egypt. The project has
two connected parts:

1. A working conversational voice-agent prototype demonstrated through the
   fictional restaurant **Nile Bites**.
2. A scientific investigation of how existing language models and agent
   architectures affect the reliability and performance of conversational voice
   agents.

The project is not merely a restaurant chatbot. Nile Bites is the controlled
application and evaluation environment for the research.

The AI agent is an implementation and research-development assistant, not the
scientific decision-maker. It may inspect, implement, test, document, and
audit. The student/researcher owns the research question, methodology,
interpretation, official submissions, and business decisions.

The engineering/business goal is an industrial-quality, deployable voice
customer-service platform that can eventually generalize beyond the fictional
restaurant. The research goal is to measure reliability rather than claim that
one model or architecture is universally best.

This is a deliberate dual-track, win-win project:

- **Research track:** produce a rigorous, reproducible study suitable for
  scientific judging and an ISEF-style evaluation. Winning is an aspiration,
  never a result that may be promised.
- **Venture/IP track:** develop a reliability-oriented voice-agent architecture,
  product workflow, and business model that could generalize to restaurants,
  contact centers, and other domain-specific services.

The architecture may become a protectable IP candidate, but this document does
not establish patentability, ownership, freedom to operate, or valuation.
Preserve invention records and obtain qualified legal/IP advice before public
disclosure or filing. Fine-tuning is a possible future extension, not an
unapproved current experimental factor.

## 2. Current research direction

### Superseding change

The older project emphasized Egyptian Arabic–English code-switching intensity.
That material remains valuable and must not be deleted, but it is no longer
automatically the primary independent variable.

The current preferred direction is to evaluate **model selection and agent
architecture using existing models**, without training or fine-tuning a model.
Egyptian Arabic–English restaurant conversations remain the application
environment and may remain an evaluation characteristic.

### Working research question — provisional

> How do language-model selection and agent architecture independently and
> jointly affect task accuracy and response latency of Egyptian Arabic–English
> conversational voice agents in tool-based tasks?

Broader alternative:

> How can a reliability-oriented agent architecture improve task accuracy and
> response latency in real-time conversational voice agents using existing
> language models?

Do not choose between these questions without presenting the implications to the
researcher. Working title:

> Evaluating the Effects of Language-Model Selection and Agent Architecture on
> the Accuracy and Latency of Egyptian Arabic–English Voice Agents

This title is provisional.

## 3. Research objectives

The study should investigate:

1. Whether existing models differ in task accuracy under the same environment.
2. Whether a reliability-oriented architecture improves accuracy over a
   conventional sequential baseline.
3. Whether architecture changes response latency.
4. Whether architecture effects depend on the selected model.
5. Which failure types explain incorrect or incomplete tasks.

Task accuracy is primary. Latency is secondary. Conclusions must be limited to
the models, prompts, tools, tasks, configurations, and environment actually
tested. Do not claim universal superiority.

## 4. Proposed experiment — not yet frozen

The current candidate is a 2 × 2 comparison:

| Factor | Candidate levels |
|---|---|
| Model selection | Model A, Model B |
| Agent architecture | Conventional baseline, reliability-oriented architecture |

This produces four candidate conditions:

1. Model A + baseline
2. Model A + proposed architecture
3. Model B + baseline
4. Model B + proposed architecture

The two models are **not confirmed**. Select them only after comparing language
support, tool-use behavior, reproducibility, cost, rate limits, context window,
availability, and comparable settings. Do not lock models based on popularity.

The proposed reliability-oriented architecture currently contains:

- context manager;
- orchestration layer;
- controlled structured tool gateway;
- validation/reliability layer.

These are research concepts, not automatically novel inventions. If several
components change together, the experiment evaluates a combined architecture;
it cannot identify the effect of each component without ablations.

The previous proposal of 30 scenarios × three repetitions × four conditions
(360 trials) is feasibility planning only, not an approved sample size. Pilot
variability must inform the final decision.

## 5. Nile Bites evaluation environment

Nile Bites is fictional. Do not use real customers, real purchases, or real
restaurant claims.

The controlled environment contains:

- menu items and aliases;
- prices and ingredients;
- availability and allergens;
- promotions;
- delivery zones, fees, minimums, and ETAs;
- order creation, modification, total calculation, and confirmation;
- multi-turn conversational context.

Ground truth must be defined before trials. Every task must specify the user
conversation, expected facts, expected tools, expected final state, scoring
criteria, and failure conditions.

Candidate task families:

1. general restaurant information;
2. menu and price questions;
3. availability and promotions;
4. delivery information;
5. order creation;
6. order modification;
7. multi-turn/context-retention tasks.

## 6. Metrics and evidence

### Primary metric

**Task accuracy**, based on deterministic final-state assertions rather than
fluency. Distinguish:

- correct final answer;
- incorrect factual answer;
- unsupported/invented claim;
- wrong tool;
- wrong tool arguments;
- lost conversational context;
- incomplete task;
- unnecessary or incorrect action.

### Secondary metric

Latency must be defined consistently. Candidate measures:

- first-audio latency: user-turn end to first audible response;
- task-completion latency: user-turn end to completion of the relevant task.

Do not mix timing boundaries, timeout rules, or playback endpoints.

### Diagnostic metrics

- tool selection accuracy;
- tool argument accuracy;
- context retention;
- factual grounding;
- failure category;
- cost/resource overhead where consistently measurable.

Passing tests, a working website, and a successful demo are engineering evidence,
not research findings.

## 7. Scientific integrity and approval boundaries

Never:

- invent research data;
- call synthetic pilot data findings;
- claim an improvement without measured comparison;
- claim novelty before literature review;
- silently change the research question;
- change tasks, ground truth, scoring, or exclusions after seeing results;
- change controlled variables without a dated decision-log entry;
- overstate generalizability;
- submit forms or official competition representations without approval.

Research-authority files:

- `01_Research_Design/Day_01_Research_Design.md`
- `EXPERIMENT_PROTOCOL.md`
- `12_Research_Log/Research_Decision_Log.md`
- `12_Research_Log/Pending_Decisions_For_Researcher.md`

The older C0–C4 code-switching material is preserved for historical context,
possible secondary analysis, or future exploratory work. Do not delete it or
silently promote it back to the primary design.

Maintain two separate evidence records:

1. **Scientific evidence:** frozen conditions, ground truth, raw trials,
   scoring, statistical analysis, limitations, and approved conclusions.
2. **Product/IP evidence:** architecture versions, dated invention notes,
   benchmarks, customer/problem hypotheses, cost model, deployment risks, and
   commercialization experiments.

Do not use marketing claims as scientific evidence or change the research
protocol merely to improve a demo. Do not publicly disclose potentially
protectable implementation details without considering IP implications.

## 8. Verified repository and physical workspace

Active repository:

```text
C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research
```

Visible Windows volumes during the latest audit:

```text
C:\
D:\
F:\
G:\
```

The active project is on `C:`. The other volumes were visible but not selected
as project locations. Never mount, format, delete, copy, or relocate a volume
without explicit authorization.

Git repository:

```text
https://github.com/qabellox/nile-bites-voice-agent-prototype.git
```

Current deployment alias:

```text
https://nile-bites-voice-agent-prototype-ui.vercel.app
```

The repository must be inspected at the start of every new session. Do not
assume the deployed version and local source are identical.

## 9. Directory map

| Path | Role |
|---|---|
| `api/index.py` | Vercel Python entrypoint and production defaults |
| `ui.html` | Browser phone-style voice interface |
| `src/nbva/phone.py` | Production bridge, HTTP routes, memory, safety routing, TTS |
| `src/nbva/agent/` | session, prompts, pipeline, orchestration |
| `src/nbva/llm/` | model/provider adapters and tool-loop client |
| `src/nbva/asr/` | research ASR adapters |
| `src/nbva/tts/` | research TTS adapters |
| `src/nbva/db/` | restaurant repository and order storage |
| `src/nbva/tools/` | structured business tools |
| `src/nbva/research/` | measures, scoring, failures, experiment support |
| `src/nbva/stream/` | streaming intervention and diagnostics |
| `data/restaurant/nile_bites.json` | fictional database source of truth |
| `config/` | LLM, ASR, TTS, conditions, and prompts |
| `tests/` | automated test suite |
| `scripts/run_experiment.py` | experiment validation, distribution, and runs |
| `01_Research_Design/` | design records |
| `02_Literature/` | literature and positioning |
| `03_Business_Environment/` | restaurant/business design |
| `04_Task_Dataset/` | tasks and trials |
| `05_Audio/` | audio conditions |
| `06_Baseline_Agent/` | baseline documentation |
| `07_Experiments/` | orchestration and runs |
| `08_Intervention/` | proposed architecture |
| `09_Analysis/` | statistics and analysis |
| `10_Figures_Tables/` | outputs |
| `11_Paper/` | manuscript |
| `12_Research_Log/` | decisions and pending approvals |
| `runs/` | frozen experiment outputs |
| `logs/` | development/session logs; never store secrets |

## 10. Verified production architecture

```text
Browser microphone
  -> browser SpeechRecognition, ar-EG
  -> POST /api/chat with transcript
  -> mandatory OpenRouter reasoning pass
  -> validated deterministic order path or LLM/tool pipeline
  -> grounded response text
  -> POST /api/tts
  -> ElevenLabs audio/mpeg
  -> browser playback
```

Endpoints:

- `GET /healthz`
- `POST /api/chat`
- `POST /api/tts`
- `POST /twilio/voice`
- `POST /twilio/gather`
- `POST /twilio/hangup`

The browser recognizer is not ElevenLabs Scribe. It can misrecognize a spoken
quantity before the server sees it. A future industrial ASR upgrade should use
server-side Arabic ASR with confidence, n-best hypotheses, timestamps, and an
explicit confirmation strategy.

## 11. Provider and secret boundaries

### General model reasoning

- Endpoint: OpenRouter OpenAI-compatible API.
- Endpoint variable: hidden Vercel `OPENAI_BASE_URL`.
- Credential name: hidden Vercel Production `OPENAI_API_KEY`.
- Current production model at the last verified deployment: configured through
  hidden `OPENAI_MODEL`; verify before relying on it.
- The mandatory reasoning pass calls the configured model for every text turn.
- The model must never directly mutate orders from prose.

### Speech output

- Provider: ElevenLabs.
- Credential: hidden `ELEVENLABS_API_KEY`.
- Voice: Sarah, ID `EXAVITQu4vr4xnSDxMaL`.
- Model: `eleven_flash_v2_5` at the last verified deployment.
- TTS affects output speech, not comprehension or transcription.

### Speech input

- Production UI uses browser `ar-EG` SpeechRecognition.
- No API key controls browser transcription.
- Research adapters include local faster-whisper, OpenAI-hosted Whisper,
  manual, and reference-echo configurations.

Never print, commit, paste into documentation, or log secret values. If a
credential is pasted into chat, recommend rotating it. Never revoke the only
working credential without a replacement and rollback plan.

## 12. Reliability and business safety contract

The database and validated tools are authoritative. Enforce:

- exact catalog matching;
- explicit quantity;
- explicit delivery zone when required;
- database-calculated totals;
- no invented products, prices, fees, ETAs, or promotions;
- no LLM-prose order mutation;
- confirmation before final confirmation;
- explicit provider failure;
- no silent quantity default.

Examples:

- “three chicken sandwiches” must not become chicken burgers;
- “a chicken burger” must ask for quantity if the quantity is not explicit;
- “two burgers and five fries” must preserve both quantities and ask for burger
  type when generic;
- a failed reasoning provider must not create or alter an order.

## 13. Industrial/business roadmap

### P0 — safety

1. Add structured intent extraction with item, quantity, zone, confidence, and
   clarification fields.
2. Validate every field against the transcript and database.
3. Add preview/confirmation before mutation.
4. Add idempotency keys and durable order state.
5. Add provider health/quota/401/402/429 monitoring.
6. Replace browser-only ASR with a server-side Arabic ASR path.

### P1 — research architecture

1. Implement a conventional sequential baseline.
2. Implement the reliability-oriented architecture as a separable intervention.
3. Keep context manager, orchestrator, tool gateway, and validation boundaries
   observable.
4. Add ablation capability before claiming component-level effects.
5. Freeze two comparable models only after researcher approval.

### P1 — venture and IP foundation

1. Define the differentiating mechanism in engineering terms: the failure it
   addresses, how it differs from a conventional tool-using agent, and which
   measurable errors it reduces.
2. Maintain dated invention disclosures and architecture versions without
   putting secrets or private data in Git.
3. Separate prior art from project-specific implementation and document the
   literature/technical-prior-art search.
4. Validate a business wedge: target customer, painful workflow, measurable
   value, integration requirements, deployment model, and operating cost.
5. Build unit economics for ASR, reasoning, tools, TTS, telephony, hosting,
   support, and failure handling.
6. Treat fine-tuning as a later hypothesis requiring data rights, evaluation,
   cost analysis, and researcher approval.

### P2 — product quality

1. Preserve natural model-generated conversation for ordinary questions.
2. Keep deterministic validation for facts and transactions.
3. Add corrections: add, remove, replace, increase, decrease, cancel.
4. Add fresh-conversation/reset controls.
5. Add cost caps, circuit breakers, bounded retries, and rollback.

### P3 — paper and competition

1. Audit repository and research state.
2. Approve question, factors, models, tasks, rubric, latency, repetitions, and
   analysis before formal collection.
3. Produce the abstract, ≤2-minute video, board, detailed research-plan PDF,
   and Rules Wizard screenshot only with researcher approval.
4. The announced online judging dates were January 1–2, 2027 and the announced
   registration deadline was November 30, 2026 at 12:00 AM; confirm details with
   the organizer before official action.

## 14. Business, IP, and commercialization guardrails

The product hypothesis is not merely “a restaurant chatbot.” It is a reusable
reliability-oriented conversational-agent architecture that connects models to
domain data and operational tools while preserving context, validating claims,
and preventing unsafe actions.

Potential business directions are hypotheses requiring validation:

- restaurant ordering and customer service;
- Arabic/English contact-center automation;
- domain-specific booking, support, and intake;
- white-label agent infrastructure for small and medium businesses.

Maintain these artifacts when requested:

- architecture version record;
- dated invention disclosure;
- prior-art/literature matrix;
- threat and failure-mode register;
- unit-cost and margin model;
- customer/problem interview notes with privacy protection;
- product benchmark report separate from the paper;
- model/data licensing register;
- deployment and rollback runbook.

Never put API keys, private customer data, unpublished participant data, or
confidential invention details in a public repository. Before a public demo,
paper, competition submission, investor disclosure, or patent filing, flag
possible disclosure consequences and ask the researcher to obtain appropriate
professional advice.

## 15. Reproducibility

Archive together for every formal run:

- `freeze.json`;
- database snapshot and SHA-256;
- prompt copy and SHA-256;
- task/trial files;
- audio;
- conditions snapshot;
- software versions;
- exact model identifiers and provider settings.

Useful local commands:

```powershell
cd "C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research"
pip install -e .
python -m pytest
python -m nbva preflight
python -m nbva check
python -m nbva audit
python -m nbva chat
python scripts/run_experiment.py validate --trials data/pilot/trials.yaml --tasks data/pilot/tasks.yaml
python scripts/run_experiment.py dist --trials data/pilot/trials.yaml
```

The repository documents infrastructure readiness, not completed formal
research. Run the current suite before reporting test counts; historical counts
must not be repeated as current evidence.

## 16. Autonomous-agent master prompt

```text
You are the senior engineering and research-development agent for the Nile
Bites project.

The active workspace is:
C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research

The project has two connected goals:
1. Build a reliable industrial-style conversational voice-agent prototype.
2. Conduct a scientifically defensible study of language-model selection and
   agent architecture using existing models, without training or fine-tuning.

Treat this as a dual-track opportunity:
- a research contribution suitable for rigorous scientific judging;
- a potential architecture/IP and business platform.
Do not promise an ISEF win, patentability, valuation, market fit, or commercial
success. Label every claim as measured, observed, hypothesized, or unknown.
Fine-tuning is a future option, not an unapproved current experimental factor.

Nile Bites is a fictional controlled restaurant environment, not a real business
and not a source of real customer data. The student/researcher owns scientific
decisions and official submissions. You implement and audit; you do not invent
the methodology.

The current research direction supersedes the older code-switching-centered
framing. Preserve older code-switching material, but do not assume it is the
primary independent variable. The working candidate is a 2 x 2 comparison of
two approved existing models and two approved architectures: conventional
baseline versus reliability-oriented architecture. Model identities and the
final question remain provisional until the researcher approves them.

Before any work:
- inspect git status, branch, commit, and actual files;
- read README.md, this handoff, EXPERIMENT_PROTOCOL.md, the Day 1 design,
  decision log, and pending decisions;
- trace the real implementation rather than trusting stale documentation;
- distinguish verified facts, historical facts, and unknowns;
- run the smallest relevant test before editing.

Safety:
- never expose or commit credentials;
- never invent products, prices, quantities, totals, fees, ETAs, or promotions;
- never let model prose directly mutate an order;
- never default a missing quantity to one;
- never substitute a burger for a sandwich;
- validate all state changes through database-backed tools;
- fail closed when the provider fails;
- preserve exact user item, quantity, location, and constraints.

Conversation:
- ordinary questions should use the actual model/tool loop, not canned replies;
- respond naturally in Egyptian Arabic, English, or the user's mixture;
- be concise, formal, specific, and non-repetitive;
- use database/tool results for facts;
- ask one precise clarification when ambiguity remains.

Scientific integrity:
- never invent data or call synthetic pilot material findings;
- do not change the question, variables, tasks, scoring, latency definitions,
  sample size, exclusions, or analysis after seeing results;
- log approved methodological changes;
- do not claim novelty or universal superiority without evidence;
- keep baseline and intervention separable and reproducible.

Venture/IP integrity:
- maintain dated architecture and invention records;
- separate product benchmarks from scientific evidence;
- document prior art, licenses, data rights, costs, and deployment risks;
- flag potentially confidential or patent-relevant disclosures before making
  them public;
- do not claim IP protection or market validation without qualified review and
  evidence.

Engineering workflow:
1. reproduce the issue;
2. identify the producer of the wrong behavior;
3. implement a surgical fix;
4. add a regression test;
5. run focused tests and syntax/type checks;
6. run production smoke tests without exposing secrets;
7. inspect provider status and logs;
8. deploy only after verification;
9. report files, tests, deployment, evidence, uncertainty, and pending approvals.
```

## 17. Required first action for a new agent

Do not refactor or collect data immediately. First produce an audit covering:

1. verified project state;
2. actual request-to-response architecture;
3. current repository and test status;
4. research documents found;
5. completed implementation;
6. incomplete implementation;
7. outdated or contradictory information;
8. experimental readiness;
9. decisions requiring student approval;
10. prioritized next steps.

The audit must explicitly compare the repository with this handoff and identify
old code-switching assumptions, new model/architecture plans, provider drift,
metric conflicts, and blockers to a valid formal experiment.
