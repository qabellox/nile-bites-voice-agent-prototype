# Agent Context Cache — Nile Bites

**Purpose:** cross-session, cross-device memory. Read this FIRST; open only the
files it points to. It stores verified state and pointers, never the full docs.
**Update rule:** refresh §1/§3/§4/§6 + the log before ending a session.
**Never store secrets here** — provider credential NAMES only.

---

## 1. Verified state — snapshot 2026-11-19

- Workspace: `C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research`
- Branch `main`, HEAD `ee000fa` ("fix(phone): let the model run the call …").
- Remotes:
  - `origin` → `github.com/qabellox/egyptian-arabic-english-voice-agent` (PRIVATE)
  - `prototype` → `github.com/qabellox/nile-bites-voice-agent-prototype` (PUBLIC)
  - Both synced at `0b122db`; `ee000fa` + the cache commit push to both.
- Deployment: `https://nile-bites-voice-agent-prototype-ui.vercel.app` —
  `/healthz` now reports `llm`, `llm_provider`, `system_prompt_file`, `engine`,
  `direct_order_path`, `context_injection` (deploy not yet re-verified).
- Suite: **456 tests, 0 failures, 0 errors, 0 skipped** (2026-11-19, `pytest`).
  Re-run before quoting counts. Note `addopts = "-q"` already: `-q -q` hides the
  summary line; use plain `pytest` to see the count.
- `.env` present, untracked; `.gitignore` covers secrets, logs, audio, run outputs.

## 2. Project identity & research direction

- **Dual track:** (1) rigorous research on **model selection × agent architecture
  using existing models** (fine-tuning is NOT an approved factor); (2) product /
  IP / venture: a reliability-oriented conversational-agent architecture.
- **Nile Bites** = fictional controlled restaurant/evaluation environment. No real
  customers, purchases, or restaurant claims.
- **The study is NOT code-switching.** Primary IVs: **agent architecture**
  (A0 baseline vs A1 reliability/efficiency) × **model capability** (weak vs
  strong). Code-switching is only an application-environment property. The old
  C0–C4 material is preserved as superseded/historical — never cite it as the
  current design.
- Working question, title, 2×2 design and component list: authority is
  `01_Research_Design/Current_Direction.md` + `EXPERIMENT_PROTOCOL.md`. Models
  are NOT confirmed. The 360-trial figure is feasibility planning only.
- **Authority files (do not edit without researcher approval):**
  `01_Research_Design/Day_01_Research_Design.md`, `EXPERIMENT_PROTOCOL.md`,
  `12_Research_Log/Research_Decision_Log.md`,
  `12_Research_Log/Pending_Decisions_For_Researcher.md`,
  `AUTONOMOUS_AGENT_HANDOFF.md`.

## 3. Verified production path (post-fix)

```
Browser mic → SpeechRecognition (ar-EG) → POST /api/chat
→ model tool loop (one LLM call per turn; order state injected as system note)
→ reply text + `sentences` (≤3 chunks)
→ client: POST /api/tts for chunk 1 → play → prefetch chunk 2 while playing
```

- `phone:` section in `config/main.yaml`: `prompt_file`, `direct_order_path:
  false`, `context_injection: true`. Read only by `PhoneBridge`, so the research
  instrument never sees it.
- Production persona: `config/prompts/phone_agent_prompt.md`. Research prompt
  (`config/prompts/baseline_system_prompt.md`) is the controlled variable and is
  untouched.
- `_direct_order_response` (keyword/template path) is still present and tested;
  reachable only with `phone.direct_order_path: true`.
- Entry `api/index.py` (Vercel): model from `OPENAI_MODEL`, default
  `nvidia/nemotron-3-ultra-550b-a55b:free` with a startup warning;
  `llm.max_tokens 500`, `temperature 0.25`, `timeout 35s`.
- Routes in `src/nbva/phone.py`: `GET /`, `GET /healthz`, `POST /api/chat`,
  `POST /api/tts`, `/twilio/voice`, `/twilio/gather`, `/twilio/hangup`.
- ElevenLabs TTS (`ELEVENLABS_API_KEY`, voice `EXAVITQu4vr4xnSDxMaL`,
  `eleven_flash_v2_5`).
- Browser ASR is **not** server-grade: it can mishear a spoken quantity before
  the server sees it. Server-side Arabic ASR with confidence/n-best = P0.
- Engineering evidence (tests, demo, healthz) ≠ research findings.

## 4. Known drifts / contradictions (open)

1. ~~Protocol framed baseline vs streaming over C0–C4~~ — RESOLVED 2026-11-18;
   archived at `01_Research_Design/_superseded/EXPERIMENT_PROTOCOL_v1_*.md`.
2. Provider drift: `config/llm.yaml` says `qwen2.5:7b-instruct` +
   `openai_compatible`; production model comes from hidden `OPENAI_MODEL`.
   `config/tts.yaml` says `edge` / `ar-EG-SalmaNeural`; production uses
   ElevenLabs. The config files describe the instrument, not production.
3. **P-10 unresolved:** latency cutoff 10s in config is dead; effective 120s;
   first-audio vs turn-completion not standardized. Blocks a valid formal run.
4. **Public visibility risk:** the public prototype repo exposes the full
   architecture, handoff, and research docs. No secret values found in a scan,
   but IP/disclosure review is required (handoff §14).
5. **No production latency logging.** First-audio / turn-completion are measured
   on the research path only; the deployed bridge logs turns, not timing.
6. `src/ui.py` is unreferenced, pre-existing, and has a syntax error
   (`render_template_string('''` never closed). Nothing imports it. Superseded by
   root `ui.html`; delete or fix only with owner approval.
7. **LEGACY:** C1–C4 boundaries provisional; dev set 16/33 off-band — belongs to
   the superseded instrument only.
8. No formal human-participant data exists. `data/pilot/` is synthetic dev-only.

## 5. Safety contract (code-enforced — never weaken)

- DB + validated tools are authoritative; no invented products, prices, fees,
  ETAs, promotions; no LLM-prose order mutation.
- Explicit quantity required (never default to 1); ask when ambiguous.
- "three chicken sandwiches" ≠ chicken burgers; preserve item + quantity + zone.
- Preview/confirm before mutation; idempotency + durable order state = P0.
- Provider failure fails closed (no order created/altered).
- Never expose/commit/log credentials; rotate if a secret is pasted in chat.

## 6. Prioritized next steps

- **Production intelligence fix: DONE** (`ee000fa`). Evidence + full change list:
  `08_Intervention/PRODUCTION_AGENT_FIX.md`. Eight defects: discarded per-turn
  LLM pass; template layer answering before the model; fries defaulting to
  quantity 1; amnesiac message history (no tool results/order state); scope gate
  refusing Arabic greetings; research prompt serving production; full-reply TTS
  before any audio; invisible model default.
- **Owner action:** set `OPENAI_MODEL` in Vercel, redeploy, check `/healthz`, then
  hold a real 5-turn call and read the transcript. Model choice is still the
  owner's; the research pair is unselected.
- **A0/A1 switch: DONE (Phase 1).** `architecture.engine` selects the arm via
  `src/nbva/arch/base.py::build_turn_engine`. A0 = frozen `BaselinePipeline`,
  `a1` raises on purpose — never silently falls back. `AgentApp.engine_id`,
  `describe()["architecture"]`. Tests: `tests/test_architecture.py`.
- **Phase 2 = A1 components**, one at a time, flag-gated + observable, no
  component-level claims until ablations run.
  - **2a gateway DONE** (`src/nbva/arch/gateway.py`): schema validation before
    dispatch, numeric-string coercion, `create_order` dedup, per-call timing.
    Parity test: `llm_schemas()` identical to the registry; `enabled=False` is a
    pure pass-through. 16 tests in `tests/test_gateway.py`.
  - **Next = 2b context manager.** Note: the production `_conversation_note` is
    deliberately NOT the A1 context manager — A1's must be measured as part of
    the intervention.
  - 2c validation, 2d orchestration (own loop; parallel reads flag default OFF),
    2e latency path, 2f assembly + no-silent-fallback suite. Spec:
    `08_Intervention/PHASE2_A1_DESIGN.md`.
- **Stats design (paired) proposed:** `08_Intervention/STATISTICAL_DESIGN_PAIRED.md`
  — randomized complete block design, task = unit, order schedule seeded +
  hashed, task-level clustering. **P-11 corrected:** a denominator of 1.0 is
  defined; the problem is zero estimated variance → anti-conservative intervals;
  bootstrap does NOT fix it. Method + boundary rule still unselected.
- **P0 safety:** structured intent extraction (item, qty, zone, confidence,
  clarification) + field validation vs transcript + DB; preview/confirm;
  idempotency; provider health/quota/401/402/429 monitoring; server-side Arabic ASR.
- **P1 venture/IP:** dated architecture + invention records; prior-art matrix;
  business wedge; unit economics; separate product benchmarks from science.
- **P3 competition:** audit → researcher approves design → abstract, ≤2-min video,
  board, plan PDF only with approval. Announced judging 2027-01-01/02;
  registration deadline 2026-11-30 00:00 — confirm with organizer.

## 7. Session protocol (cheap memory)

1. Read this file, then `git status`, `git log -3`, `git remote -v`.
2. Open an authority file only if editing/verifying it; task files are pointers.
3. Quote test counts only from a fresh run.
4. Update §1/§3/§4/§6 and the log below at session end; keep this file compact.
5. Any scientific change → researcher approval → decision log, never silently.

## Log

| Date | Commit | Change | Verified by |
|---|---|---|---|
| 2026-11-18 | a80912e | Cache created after handoff audit | pytest 418/0; /healthz ok |
| 2026-11-18 | 1716cf5 | Code-switching removed as primary design; new Current_Direction + protocol; old archived | doc review; 418/0 |
| 2026-11-18 | fec54a9 | `IMPLEMENTATION_PLAN_A0_A1_P0.md` written — no code touched | doc review |
| 2026-11-18 | 08c1a50 | Researcher decisions recorded; Phase 1 engine switch implemented | 428/0; a1 refuses; A0 parity |
| 2026-11-18 | 0b122db | Paired stats design + Phase 2 spec; P-11 corrected; Stage 2a gateway | 444/0; schema parity |
| 2026-11-19 | ee000fa | Production agent fix: model drives every turn, order state injected, progressive speech, model visible in `/healthz` | 456/0 (12 new phone tests); node --check ui.html |
