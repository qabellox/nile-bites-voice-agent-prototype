# Twilio phone setup — Nile Bites voice agent

**Status (2026-11-19):** the application side is built, deployed and verified.
There is **no Twilio account, number or credential yet**, so no real phone call
has been placed. This document is the handoff for the account owner.

## What is already working (evidence, not assumption)

`https://nile-bites-voice-agent-prototype-ui.vercel.app` is live and the web UI
is unchanged. These webhooks were called over HTTPS and returned HTTP 200 with
valid TwiML:

| Webhook | Verified result |
|---|---|
| `POST /twilio/voice` | `<Gather input="speech" language="ar-EG" ...>` + greeting |
| `POST /twilio/gather` | Arabic `SpeechResult` reached Gemini and produced a real reply |
| `POST /twilio/hangup` | ends the in-memory call session |

Routing uses the existing Vercel routes in `vercel.json`; no domain or UI was
replaced.

### Defect found and fixed before the first call

The old TwiML used `<Say language="ar-EG">`. Twilio has **two separate language
tables**: `ar-EG` is valid for `<Gather>` (recognition) but **not** for `<Say>`
(synthesis). Twilio's Arabic `<Say>` codes are `ar-AE`, `ar-XA` and `arb`. An
invalid language/voice pair can make `<Say>` fail, which would have made the
first call silent. Now:

- recognition: `ar-EG` (Egyptian Arabic) + hints for dish/zone names;
- synthesis: `language="ar-XA" voice="Google.ar-XA-Standard-A"` (Modern Standard
  Arabic, female, standard tier — not beta).

Twilio has **no Egyptian-Arabic TTS voice**. The caller can speak Egyptian
Arabic; the reply will sound Modern Standard Arabic. That is a platform limit,
not a prompt bug. Override with `TWILIO_TTS_LANGUAGE` / `TWILIO_TTS_VOICE` if a
better voice becomes available.

## Blocker — must be done by an adult/guardian

I am 16. Twilio requires an account holder who is 18+, a payment method for
anything after the free trial, and a verified phone number. **I cannot create
the account, buy the number or verify a mobile by myself.** The account must be
owned by بشبابها or by a parent/guardian. Any revenue or payment sharing needs a
written agreement co-signed by the guardian.

## Exact console steps (account owner)

1. Sign up at <https://www.twilio.com/try-twilio>. The trial gives **$15 credit
   with no credit card**. Keep the Account SID and Auth Token private.
2. In Console → **Phone Numbers → Manage → Verified Caller IDs**, verify the
   mobile that will make the test call. On a trial account Twilio will not
   accept calls from an unverified number.
3. Console → **Phone Numbers → Buy a number**. Filter on **Voice**.
   - A **US local number** is the fastest demo path: **$1.15/month**, inbound
     **$0.0085/min**. Calling it from an Egyptian mobile is an international
     call charged by that mobile operator.
   - Egyptian `+20` numbers may require an Exclusive Number order with business
     KYC documents. Check what the Console actually offers; do not assume one
     is available.
4. Open the bought number → **Voice & Fax** section:
   - **A call comes in** → webhook → `HTTP POST`
   - URL:
     `https://nile-bites-voice-agent-prototype-ui.vercel.app/twilio/voice`
   - Save. Leave the other webhooks alone; `/twilio/gather` and
     `/twilio/hangup` are returned as relative actions inside the TwiML.
5. Call the number from the verified mobile. First Twilio webhook → greeting →
   say something like «بتوصلوا مدينة نصر؟» → the Gemini agent answers.

If the account owner prefers API setup instead of clicking, that needs the
Account SID + Auth Token and the number SID; do not paste those into chat.

## Optional hardening and tuning (Vercel → Project → Environment Variables)

| Variable | Default | Purpose |
|---|---|---|
| `TWILIO_ASR_LANGUAGE` | `ar-EG` | Recognition language for `<Gather>` |
| `TWILIO_TTS_LANGUAGE` | `ar-XA` | **Must** be a valid `<Say>` language |
| `TWILIO_TTS_VOICE` | `Google.ar-XA-Standard-A` | Twilio voice name |
| `TWILIO_ASR_HINTS` | dish + zone names | Improves recognition of menu items |
| `TWILIO_VALIDATE_SIGNATURES` | off | Set to `1` to require `X-Twilio-Signature` |
| `TWILIO_AUTH_TOKEN` | none | Required only when validation is on |

Signature validation is **off by default** so a typo cannot block the first
call. Turn it on after the first successful call: it checks Twilio's HMAC-SHA1
signature (sorted POST fields, constant-time compare) and rejects forged
requests to `/twilio/*`. The public web otherwise stays open for the browser UI.

After changing env vars: `vercel --prod --yes` (or redeploy in the dashboard).

## Cost estimate for the demo

Twilio free trial: $15 credit, no card. After the trial, pay-as-you-go, no
platform fee; the number has a monthly charge even with zero calls. Published
US rates as of late 2026 (verify in your Console before spending):

- US local number: **$1.15/month**
- inbound on that number: **$0.0085/minute**, partial minutes rounded up
- a 4-minute test call ≈ **$0.034** on Twilio, plus the caller's international
  mobile charges and the Gemini free-tier quota used by the tool loop.

The Gemini free tier in production has per-model quota and may use inputs to
improve Google products. Fine for this fictional restaurant. **Not for real
participant data** — that needs a paid tier or another provider and a guardian's
payment method.

## Debugging a failed call

1. Twilio Console → **Monitor → Logs → Calls**: look for the webhook status.
   A non-2xx or a timeout means Twilio never got valid TwiML.
2. `vercel logs --json`: look for `turn failed:` (provider error) and for
   `POST /twilio/gather` 200 lines.
3. `GET /healthz`: must report `llm_provider: google`,
   `llm: gemini-3.5-flash-lite`, `direct_order_path: false`.
4. If `<Say>` errors in Twilio's logs, the language/voice pair is invalid —
   check `TWILIO_TTS_LANGUAGE` and `TWILIO_TTS_VOICE`.
5. If the caller hears the "can't hear you" fallback repeatedly, the issue is
   Twilio ASR, not the agent: check `language="ar-EG"` and the `hints` attribute.

## Tests

`469 passed` locally (`python -m pytest`), including TwiML language separation,
XML escaping, and signature-validation accept/reject cases.
