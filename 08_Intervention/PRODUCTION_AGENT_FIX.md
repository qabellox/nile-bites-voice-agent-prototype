# Production phone agent — defect diagnosis and fix

**Date:** 2026-11-19
**Scope:** the deployed browser/Twilio prototype (`src/nbva/phone.py`, `ui.html`,
`api/index.py`, `config/prompts/phone_agent_prompt.md`).
**Not in scope:** the research instrument (A0/A1 trial path), the frozen task
script, the frozen prompt, and the statistical design.

The owner reported the deployed agent as "scripted, latent, and unable to lead a
conversation". Every claim below was verified in the code on `main` before it was
changed. Line references are to the pre-fix tree.

---

## What was actually wrong

| # | Defect | Evidence (pre-fix) | Effect on the caller |
|---|--------|--------------------|----------------------|
| 1 | **A full LLM call per turn whose result was thrown away** | `PhoneBridge._mandatory_reasoning_pass` called `llm.complete(...)` with `tools=None`, then discarded the return value. `process_text` called it before anything else. | One extra model round trip per turn, every turn, and on any failure the turn was refused outright ("خدمة الذكاء الاصطناعي مش متاحة"). Latency and token cost doubled for zero information. |
| 2 | **Keyword/template layer answered before the model** | `_direct_order_response` (~145 lines of `in`-matching plus hardcoded Egyptian-Arabic strings) ran before the pipeline. It handled ordering, totals, confirmation, and cancellation. | For the most common utterances the model was never consulted. This is the "programmed answers based on certain questions" behaviour, literally. |
| 3 | **Silent quantity default** | `_find_order_lines` used `quantity = _quantity_from_text(...) if match else 1` for fries. | A caller who said "برجر حلومي وبطاطس" without a number got one fries line they never asked for, written into a real order. This violates the project's own rule: never default a missing quantity. |
| 4 | **Model amnesia between turns** | `Session.message_history()` returns `role`/`content` only. Tool results and order state were never in the message list. | The model could not see the order it had just built. It re-searched the menu, re-quoted prices it had already given, and could not say "same order again". This alone makes a model look dumb regardless of its capability. |
| 5 | **Out-of-scope gate rejected natural openings** | `unrelated_arabic = has_arabic and not has_context and not any(restaurant marker)` | First-turn Arabic greetings, "معلش سؤال", "إزيك" were answered with the fixed refusal. A conversation cannot start if the opening is refused. |
| 6 | **The research prompt was serving production traffic** | `config/llm.yaml` → `system_prompt.file: config/prompts/baseline_system_prompt.md`, whose first paragraph says the assistant is "being evaluated" in a study. | The deployed agent was told it was a trial subject, not a restaurant line. Wrong persona, wrong priorities. |
| 7 | **Client-side latency stack** | `ui.html` waited for the full `/api/chat` reply, then made a separate `/api/tts` call for the whole reply, then played it. | First audible word = chat completion + full reply synthesis. Two serial network waits before any sound. |
| 8 | **Deployed model was invisible, and defaulted to a free one** | `api/index.py`: `os.environ.get("OPENAI_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")`; nothing reported which model was live. | The owner could believe a strong model was running while the free default was answering. No way to tell from the UI or the API. |

Defect 3 is a correctness/safety bug. Defects 1, 4, 5, 6 are the "dumb" the
owner described. Defect 2 is the "scripted". Defects 1 and 7 are the "latent".

---

## What changed

### 1. The model drives every turn (defects 1 and 2)

- `_mandatory_reasoning_pass` deleted. `process_text` now makes exactly **one**
  model invocation per turn, inside the normal tool loop. Tested by counting
  provider calls: `test_each_turn_costs_exactly_one_model_call`.
- `_direct_order_response` still exists — it encodes real safety rules — but is
  reachable only when `phone.direct_order_path` is `true`. Default is `false`.
  The five tests that document its behaviour now request it explicitly.

### 2. The model sees the state it built (defect 4)

- New `_conversation_note(session)` renders the active order (id, status, mode,
  zone, every line with item id / quantity / unit price / line total, subtotal,
  discount, delivery, total, applied promotions) as a second system message.
- `BaselinePipeline.process_turn(..., context_note=None)`: when a note is given
  it is inserted after the system prompt; when it is `None` the message list is
  **byte-identical to before**. The grounding check accepts numbers that appear
  in the note (`extra_text`), because those numbers came from tool output in an
  earlier turn, not from the model's imagination.
- Controlled by `phone.context_injection` (default `true`).
  Tested by `test_model_receives_session_state_note_on_later_turns`.

### 3. No invented quantities (defect 3)

- `_find_order_lines` requires an explicit quantity for fries; no match means no
  line. Tested by `test_fries_line_requires_an_explicit_quantity`.

### 4. Scope is the model's job (defect 5)

- `_out_of_scope` now fires only when the utterance names an obviously unrelated
  domain (`طائرة`, `صيانة`, `programming`, …) **and** never when the caller also
  mentions the restaurant. The `unrelated_arabic` clause is gone.
  Tested by `test_greeting_is_answered_not_refused`.

### 5. A production persona (defect 6)

- New `config/prompts/phone_agent_prompt.md`: hold the call, one question at a
  time, lead with the answer then the next step, never repeat, never invent a
  fact, short spoken sentences, Egyptian Arabic by default and mirror the caller.
- `PhoneBridge` reads `phone.prompt_file` and overrides `system_prompt.file` for
  the prototype only. The frozen research prompt is untouched and is still what
  the experiment loads.
  Tested by `test_phone_uses_production_persona_prompt`.

### 6. Progressive speech (defect 7)

- `/api/chat` returns `sentences`: the reply split on sentence boundaries, max 3
  parts, lossless (`" ".join(parts)` reproduces the text).
- `ui.html` synthesizes chunk 1 immediately, plays it, and prefetches chunk 2
  while chunk 1 is playing. First audible output no longer waits for the whole
  reply. Prefetch errors are swallowed per chunk so a later chunk failure cannot
  produce an unhandled rejection mid-playback.
  Tested by `test_split_for_speech_is_ordered_and_lossless` and
  `test_chat_endpoint_returns_speech_chunks`.

### 7. Configuration is visible (defect 8)

- `GET /healthz` now reports `llm`, `llm_provider`, `system_prompt_file`,
  `engine`, `direct_order_path`, `context_injection`.
- `api/index.py` logs a warning when `OPENAI_MODEL` is unset instead of silently
  falling back. `llm.max_tokens` raised 360 → 500: a tool-calling turn has to fit
  the tool-call JSON *and* the spoken reply, and 360 truncated Arabic replies.
  Tested by `test_health_reports_the_live_model_and_answering_layer`.

Also removed: `_VAGUE_RESPONSE_MARKERS` (unused before this change). `_arabic_safe_clarification`
was unused and is now wired in: an empty model reply with no error asks a
concrete clarifying question instead of a generic apology.

---

## Verification

- **456 tests pass, 0 failures** (was 444; 12 new phone tests, all listed above
  by name).
- `config/prompts/baseline_system_prompt.md` is unmodified.
- `config/main.yaml` gains a `phone:` section and nothing else; the research
  sections and `architecture.engine: "a0"` are unchanged.
- A0 parity: with `context_note=None` (every research call site), the message
  list and the grounding check are unchanged. The new parameter is optional and
  defaults to the old behaviour.
- `node --check` on the extracted `ui.html` script passes.

---

## Deployment verification (same day, live)

I set `OPENAI_MODEL` in Vercel Production and redeployed. Two findings from the
live deployment:

**1. Paid models return `402 Payment Required`.** With `openai/gpt-4.1` live,
every turn failed and the caller heard the template fallback
(`_direct_fallback_response`) or the apology line. `vercel logs` shows:

```
HTTP Request: POST https://openrouter.ai/api/v1/chat/completions "HTTP/1.1 402 Payment Required"
```

A 402 means the key authenticated but the OpenRouter account has no credit. The
free model (`nvidia/nemotron-3-ultra-550b-a55b:free`) works on the same key, so
the account is valid and the gap is money, not configuration. Production is
therefore on the free model until credits are added.

**2. Live conversation on the free model** (fresh call, one session, after the
grounding fix was deployed):

| Turn | Caller | Reply (abridged) | Wall time | Tools |
|------|--------|------------------|-----------|-------|
| 1 | "إنتوا بتوصلوا مدينة نصر؟ وفيه إيه برجر عندكم؟" | zone fee ٢٥، minimum ١٢٠، 35–50 min, then the five burgers with prices | 10.6 s | `get_delivery_info`, `search_menu` |
| 2 | "عايز اتنين برجر حلومي" | "اتنين برجر حلومي = ٣٥٠ جنيه… محتاج المنطقة بالظبط في مدينة نصر" | 6.4 s | `get_price` |
| 3 | "تمام، أكده" | repeats only the missing field, does **not** create the order without a zone | 2.5 s | — |

`grounding_violations` was `[]` on all three turns. This is the behaviour that was
missing: it answers both questions, keeps state, asks for the one blocking field,
and refuses to invent a zone. Latency of 2.5–10.6 s per turn is the free model's
time, not the architecture's; the turn that needed no tool call was the fastest.

**3. New defect found by the live run (defect 9): grounding compared text, not
numbers.** Every correct reply that spoke a price in Arabic-Indic digits (`٣٥٠`)
was recorded as an *ungrounded number*, because `_MONEY_RE` matches Unicode digits
(`\d` is Unicode-aware) while the allowed set held ASCII digits from the tool JSON.
Fixed by translating Arabic-Indic and Extended Arabic-Indic digits to ASCII on both
sides before comparison. This affects the recorded metric, not only production: it
was inflating `grounding_violations` on the most common kind of correct Arabic
reply. Tests: `test_arabic_numeral_prices_are_compared_as_the_same_number`,
`test_an_arabic_numeral_price_the_tools_never_returned_is_still_flagged`.

---

## Switch to Google Gemini (same day, live)

OpenRouter free gives 50 requests/day and paid models return 402 with zero
credit. I switched production to the Gemini free tier instead. The provider is
now env-selected (`LLM_PROVIDER=google`, `GOOGLE_MODEL`, `GEMINI_API_KEY`), so
switching back needs no code edit. Four real defects blocked that switch; all
were found by running the exact production path, not by reading code:

| # | Symptom | Cause | Fix |
|---|---------|-------|-----|
| 10 | Every tool turn failed locally; menu came from a template | `GoogleProvider` sent `role: "tool"` as plain user text and parsed only `.text` | Translate to `functionCall` / `functionResponse` parts; parse parts, not `.text` |
| 11 | HTTP 400 `INVALID_ARGUMENT` on every tool call | Tool schemas carry `additionalProperties: False`; Gemini rejects the whole request on unknown schema keys | Filter parameter schemas to Gemini's supported keywords (recursively) |
| 12 | HTTP 500 `ModuleNotFoundError: No module named 'google'` on Vercel | `@vercel/python` installs from **pyproject.toml**, not requirements.txt; `google-genai` was only in an optional extra | Add it to base dependencies in `pyproject.toml` |
| 13 | HTTP 400 again on Vercel while local worked | `google-genai>=1.0` resolved to 3.6.1 on Vercel; local had 2.11.0 | Pin `google-genai==2.11.0` in both dependency files |
| 14 | Gemini 3 rejected the second tool round: "Function call is missing a thought_signature" | Gemini 3 returns an opaque signature on each `functionCall` part; replaying the call without it is a 400 | Cache signatures by call id (name as fallback) and reattach them when translating the transcript |

Also discovered: `gemini-3.5-flash-lite` answers 400 to `thinking_config` at all,
and `gemini-2.5-flash` free tier is capped at **5 requests/minute and 20/day**
(the 3-turn probe uses 5+ LLM requests). Config now sends `thinking_budget: null`
so one env switch works on both models. A 429 from that quota was observed live
and logged; it is a quota, not a code fault. Steps 12–14 each needed a redeploy
and a live probe to find — none showed up in the 467-test suite until the tests
were written against the observed requests.

**Model choice: `gemini-3.5-flash-lite`.** `gemini-2.5-flash` worked but burned
the daily free quota during verification; `gemini-3.8-flash` returned 503 under
high demand. The lite model produced more natural Egyptian Arabic in the same
probe and stayed under a second per model call.

**Live conversation (fresh call, one session, deployed on Vercel):**

| Turn | Caller | Reply (abridged) | Wall time | Tools |
|------|--------|------------------|-----------|-------|
| 1 | "إنتوا بتوصلوا مدينة نصر؟ وفيه إيه برجر عندكم؟" | zone + ETA, three burgers with real prices | 3.4 s | `get_delivery_info`, `search_menu` |
| 2 | "عايز اتنين برجر حلومي" | "برجر الحلومي المشوي متاح وسعره ١٧٥ جنيه. أسجل لحضرتك اتنين منه؟" | 1.9 s | `search_menu` |
| 3 | "تمام، أكده" | order registered, ٣٥٠ جنيه, free delivery, ٣٥–٥٠ دقيقة | 3.3 s | `get_delivery_info`, `create_order`, `confirm_order` |

`grounding_violations = []` on all three turns; no template fallback and no
apology line. `/healthz` reported `llm_provider: google`,
`llm: gemini-3.5-flash-lite`, production prompt, `direct_order_path: false`.

## What this does **not** fix

- **Free-tier quota.** The Google free tier is per-model per-day (20 RPD on
  2.5-flash), and a tool loop can use several requests per spoken turn. A busy
  demo will hit 429. Paid tier removes that limit but needs a card in the
  account owner's name (adult/guardian for a minor).
- **Free-tier privacy.** Google may use free-tier inputs to improve products.
  Fine for this fictional restaurant; **not** for real participant data. Use a
  paid tier or a different provider for the study.
- **OpenRouter credit.** Paid models return 402 on this account, so OpenRouter is
  still unusable for paid inference. Adding credit needs a card in the account
  owner's name (adult/guardian for a minor). OpenRouter remains the intended
  study backbone because one key reaches many vendors; it is not currently the
  prototype's provider.
- **Model choice.** If the deployment runs a weak or free model, it will still
  answer like a weak model. `/healthz` makes that visible; picking the model is
  the owner's decision, and the research pair is still unselected.
- **ASR quality.** Browser `ar-EG` recognition mangles Egyptian dialect; the
  agent cannot recover words it never received. Twilio's own ASR has the same
  limit.
- **Barge-in / interruption.** The client stops playback when a new turn starts,
  but the model is not told that its previous reply was cut off.
- **Production latency instrumentation.** First-audio and turn-completion are
  measurable on the research path, not yet logged per production turn.
- **`src/ui.py`** is an unreferenced, pre-existing file with a syntax error
  (`render_template_string('''` never closed). Nothing imports it; left alone
  because it is not part of this change.
