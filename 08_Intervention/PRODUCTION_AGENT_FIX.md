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

## What this does **not** fix

- **Model choice.** If the deployment runs a weak or free model, it will still
  answer like a weak model. `/healthz` now makes that visible; picking the model
  is the owner's decision, and the research pair is still unselected.
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
