# Agent Context Cache — Nile Bites

**Purpose:** cross-session, cross-device memory. Read this FIRST; open only the
files it points to. It stores verified state and pointers, never the full docs.
**Update rule:** refresh §1/§4/§6 + the log before ending a session. Keep ≤130 lines.
**Never store secrets here** — provider credential NAMES only.

---

## 1. Verified state — snapshot 2026-11-18

- Workspace: `C:\Users\Qebaa\Documents\Default Project\Egyptian_Arabic_English_Voice_Agent_Research`
- Branch `main`, HEAD `1716cf5` ("docs: supersede code-switching design with
  architecture x model-capability direction").
- Remotes:
  - `origin` → `github.com/qabellox/egyptian-arabic-english-voice-agent` (PRIVATE)
  - `prototype` → `github.com/qabellox/nile-bites-voice-agent-prototype` (PUBLIC)
  - At audit: HEAD == prototype/main; origin was 31 commits behind. The cache
    commit re-syncs both.
- Deployment: `https://nile-bites-voice-agent-prototype-ui.vercel.app`
  `/healthz` → `{ok:true, service:"nbva-phone-prototype"}` (verified 2026-11-18).
- Suite: **428 tests, 0 failures, 0 errors, 0 skipped, exit 0**
  (verified 2026-11-18 via `--junitxml`). Re-run before quoting counts.
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
- **Research authority files (do not edit without researcher approval):**
  - `01_Research_Design/Day_01_Research_Design.md`
  - `EXPERIMENT_PROTOCOL.md`
  - `12_Research_Log/Research_Decision_Log.md`
  - `12_Research_Log/Pending_Decisions_For_Researcher.md`
  - `AUTONOMOUS_AGENT_HANDOFF.md`

## 3. Verified production path (handoff §10, code-checked)

```
Browser mic → browser SpeechRecognition (ar-EG) → POST /api/chat {transcript}
→ mandatory OpenRouter reasoning pass → validated deterministic order path
  OR LLM/tool loop (DB-backed tools only) → grounded response text
→ POST /api/tts → ElevenLabs audio/mpeg → playback
```

- Routes in `src/nbva/phone.py`: `GET /`, `GET /healthz`, `POST /api/chat`,
  `POST /api/tts`, `POST /twilio/voice`, `POST /twilio/gather`,
  `POST /twilio/hangup`.
- Entry: `api/index.py` (Vercel). UI: `ui.html`. Bridge: `src/nbva/phone.py`.
- Providers: OpenRouter OpenAI-compatible (hidden `OPENAI_BASE_URL`,
  `OPENAI_API_KEY`, `OPENAI_MODEL`); ElevenLabs TTS (`ELEVENLABS_API_KEY`,
  voice Sarah `EXAVITQu4vr4xnSDxMaL`, model `eleven_flash_v2_5` at last deploy).
- Browser ASR is **not** server-grade: it can mishear a spoken quantity before
  the server sees it. Server-side Arabic ASR with confidence/n-best = P0.
- Engineering evidence (tests, demo, healthz) ≠ research findings.

## 4. Known drifts / contradictions (open)

1. ~~`EXPERIMENT_PROTOCOL.md` still frames baseline vs streaming over C0–C4.~~
   **RESOLVED 2026-11-18:** protocol rewritten for architecture × model
   capability; old version archived at
   `01_Research_Design/_superseded/EXPERIMENT_PROTOCOL_v1_code_switching.md`;
   Day 1 doc carries a SUPERSEDED banner; decision log updated.
2. Provider drift: `config/llm.yaml` default model is `qwen2.5:7b-instruct` /
   `openai_compatible`; production model comes from hidden `OPENAI_MODEL`
   (gpt-4o-mini per prior verified runs — re-verify). `config/tts.yaml` says
   `edge` / `ar-EG-SalmaNeural`, but the production path uses ElevenLabs. The
   config files describe the research instrument, not necessarily production.
3. `origin` (private) was 31 commits behind `prototype` (public) at audit.
4. **P-10 unresolved:** latency cutoff 10s in config is dead; effective 120s;
   first-audio vs turn-completion not standardized. Blocks a valid formal run.
5. **Public visibility risk:** the public prototype repo exposes the full
   architecture, handoff, and research docs. No secret values found in a scan,
   but IP/disclosure review is required (handoff §14).
6. **LEGACY:** C1–C4 boundaries provisional; dev set 16/33 off-band — belongs to
   the superseded C0–C4 instrument only.
7. No formal human-participant data exists. `data/pilot/` is synthetic dev-only.

## 5. Safety contract (code-enforced — never weaken)

- DB + validated tools are authoritative; no invented products, prices, fees,
  ETAs, promotions; no LLM-prose order mutation.
- Explicit quantity required (never default to 1); ask when ambiguous.
- "three chicken sandwiches" ≠ chicken burgers; preserve item + quantity + zone.
- Preview/confirm before mutation; idempotency + durable order state = P0.
- Provider failure fails closed (no order created/altered).
- Never expose/commit/log credentials; rotate if a secret is pasted in chat.

## 6. Prioritized next steps

- **A0/A1 switch: DONE (Phase 1).** `architecture.engine` in `config/main.yaml`
  selects the arm via `src/nbva/arch/base.py::build_turn_engine`. A0 = frozen
  `BaselinePipeline` (untouched file), `a1` raises `NotImplementedError` on
  purpose — never silently falls back. `AgentApp.engine_id`,
  `describe()["architecture"]`. Tests: `tests/test_architecture.py`.
- **Phase 2 = A1 components**, one at a time, flag-gated + observable, no
  component-level claims until ablations run.
- **P0 safety:** structured intent extraction (item, qty, zone, confidence,
  clarification) + field validation vs transcript + DB; preview/confirm;
  idempotency; provider health/quota/401/402/429 monitoring; server-side Arabic ASR.
- **P1 research:** implement separable conventional baseline (A0) + reliability
  architecture (A1); keep context manager / orchestrator / tool gateway /
  validation observable; add ablations before claiming component effects; freeze
  two comparable models only after researcher approval. Fine-tuning is NOT a
  factor.
- **P1 venture/IP:** dated architecture + invention records; prior-art matrix;
  business wedge; unit economics; separate product benchmarks from science.
- **P3 competition:** audit → researcher approves design → abstract, ≤2-min video,
  board, plan PDF only with approval. Announced judging 2027-01-01/02;
  registration deadline 2026-11-30 00:00 — confirm with organizer.

## 7. Session protocol (cheap memory)

1. Read this file, then `git status`, `git log -3`, `git remote -v`.
2. Open an authority file only if editing/verifying it; task files are pointers.
3. Quote test counts only from a fresh `--junitxml` run.
4. Update §1/§4/§6 and the log below at session end; keep this file compact.
5. Any scientific change → researcher approval → decision log, never silently.

## Log

| Date | Commit | Change | Verified by |
|---|---|---|---|
| 2026-11-18 | a80912e | Cache created after handoff audit | pytest 418/0; /healthz ok; git remotes |
| 2026-11-18 | 1716cf5 | Removed code-switching as primary design; new Current_Direction + rewritten protocol; old archived; decision log updated | doc review; tests 418/0 |
| 2026-11-18 | plan commit | `08_Intervention/IMPLEMENTATION_PLAN_A0_A1_P0.md` written (H3 margin options, H2 falsifiable, claim scope, cost method, A0/A1 parity, P0, phases) — **awaiting researcher approval; no code touched** | doc review; no code changes |
| 2026-11-18 | (this commit) | Researcher decisions recorded (ratio ≥0.90, task-script confirmation, LLM+hosted TTS, first-audio-ready) + P-11 opened; Phase 1 engine switch implemented | pytest 428/0; a1 refuses; A0 parity test |
