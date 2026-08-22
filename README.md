# Personal AI Telephone Agent

## Overview

This Python application places outbound telephone calls through Twilio and conducts goal-directed voice conversations with the OpenAI Realtime API. For each call, the operator supplies a destination, objective, context, preferences, and constraints. The application provides a small server-rendered dashboard, durable call history, live transcript updates, captured facts, a structured post-call summary, and optional recording.

The agent may gather and clarify information autonomously, but it does not create commitments for the owner unless that call explicitly authorizes the action. It identifies itself as a virtual assistant acting for the owner and defaults to European Portuguese (`pt-PT`).

## Architecture

```text
Browser ──HTTP/SSE──> FastAPI ───────────────> SQLite
                         │                         ▲
                         │ Twilio REST             │ call history
                         v                         │
                      Twilio Voice <──PSTN──> remote telephone
                         │
                 bidirectional Media Stream
                         │ WSS (JSON + base64 PCMU)
                         v
                      FastAPI <────WebSocket────> OpenAI Realtime
                         │
                         ├──Responses API───────> post-call summary
                         └──Twilio Recording API> optional recording
```

FastAPI owns both streaming connections and all credentials. Twilio places the PSTN call and carries bidirectional audio. OpenAI Realtime handles speech, transcription, turn detection, and responses. SQLite stores call configuration, final transcripts, facts, events, summaries, and recording metadata. The bridge forwards G.711 μ-law (`audio/pcmu`), 8 kHz, mono audio directly in base64 form; it does not transcode audio. See [Architecture](docs/ARCHITECTURE.md) for lifecycle and interruption details.

## Requirements

- Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/)
- A Twilio account and a Twilio voice-capable telephone number
- An OpenAI API key with access to the configured Realtime, transcription, and summary models
- A public HTTPS/WSS address for local development (for example, ngrok)

The application has no OS-specific runtime dependency. The automated release checks run on Linux; the commands below also show PowerShell setup. Twilio trial accounts can generally call only verified destination numbers and may have other trial restrictions; consult the Twilio Console if a trial call is rejected.

## Installation

```bash
git clone <repository-url>
cd AI-phone-assistant-v2
uv sync
cp .env.example .env
```

Windows PowerShell uses:

```powershell
git clone <repository-url>
Set-Location AI-phone-assistant-v2
uv sync
Copy-Item .env.example .env
```

Edit `.env`; never commit it.

## Environment configuration

Empty values in `.env` are treated as unset. Provider credentials are server-side only.

| Variable | Purpose / default |
|---|---|
| `APP_BASE_URL` | Public HTTPS origin, without a path; required for live calls, for example `https://example.ngrok-free.app`. |
| `APP_HOST`, `APP_PORT` | Bind values for an explicit Uvicorn command; defaults `0.0.0.0`, `8000`. |
| `APP_TIMEZONE` | Display timezone reserved for UI formatting; UTC is stored internally. Default `Europe/Lisbon`. |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`; default `INFO`. |
| `DATABASE_URL` | Async SQLAlchemy URL; default `sqlite+aiosqlite:///./ai_phone_assistant.db`. |
| `TWILIO_ACCOUNT_SID` | Twilio project Account SID; required for live calls. |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token; required for live calls, signature checks, and recording retrieval. |
| `TWILIO_PHONE_NUMBER` | Voice-capable Twilio caller ID in E.164 format. |
| `TWILIO_VALIDATE_SIGNATURES` | Validate Twilio HTTP and WebSocket signatures; default `true`. Disable only for controlled local tests. |
| `OPENAI_API_KEY` | Server-side OpenAI API key; required for live conversations and summaries. |
| `OPENAI_REALTIME_MODEL` | Realtime speech model; default `gpt-realtime-2.1`. |
| `OPENAI_REALTIME_VOICE` | Realtime voice; default `marin`. |
| `OPENAI_TRANSCRIPTION_MODEL` | Input transcription model; default `gpt-live-transcribe`. |
| `OPENAI_SUMMARY_MODEL` | Responses API text model for structured reports; default `gpt-5.6-luna`. |
| `OPENAI_REALTIME_VAD_TYPE` | `semantic_vad` (default) or `server_vad`. |
| `OPENAI_REALTIME_VAD_EAGERNESS` | Semantic VAD response timing: `low`, `medium`, `high`, or `auto`. |
| `OPENAI_REALTIME_VAD_THRESHOLD` | Server VAD speech threshold; default `0.5`. |
| `OPENAI_REALTIME_VAD_PREFIX_PADDING_MS` | Audio retained before detected speech; default `300`. |
| `OPENAI_REALTIME_VAD_SILENCE_DURATION_MS` | Server VAD end-of-turn silence; default `700`. |
| `DEFAULT_RECORDING_POLICY` | `off`, `ask`, or `always`; default `ask`. |
| `DOWNLOAD_RECORDINGS_LOCALLY` | Save completed recordings under `RECORDINGS_DIR`; default `false`. |
| `RECORDINGS_DIR` | Private recording directory; default `./data/recordings`. |
| `DRY_RUN` | Validate and persist a simulated call without contacting providers; default `false`. |

`uvicorn app.main:app --reload` uses Uvicorn's own default bind settings. To apply the configured bind values explicitly on Unix, run `uv run uvicorn app.main:app --reload --host "$APP_HOST" --port "$APP_PORT"` after exporting them, or pass literal values.

## Twilio setup

1. In the Twilio Console, copy the **Account SID** and **Auth Token** into the matching `.env` variables.
2. Under **Phone Numbers**, buy or select a number with Voice capability. Put that Twilio-owned number, including `+` and country code, in `TWILIO_PHONE_NUMBER`.
3. Trial users should verify the destination telephone in the Console before testing.
4. Start the application and public tunnel described below.

No inbound Voice webhook needs to be configured on the Twilio number. For every outbound call, the application dynamically supplies these public callbacks:

- `POST /twilio/voice?call_id=<internal-uuid>` returns TwiML with `<Connect><Stream>`;
- `POST /twilio/call-status?call_id=<internal-uuid>` receives call lifecycle changes;
- `WSS /twilio/media` carries the bidirectional Media Stream;
- `POST /twilio/recording-status?call_id=<internal-uuid>` receives recording status.

Twilio cannot reach `localhost`, so `APP_BASE_URL` must resolve publicly over HTTPS. The application converts its `https://` origin to `wss://` for the Media Stream. Keep signature validation enabled in normal operation; the exact public URL Twilio signs must match the URL FastAPI reconstructs through the tunnel/proxy.

## Local tunnel

With [ngrok](https://ngrok.com/) installed:

```bash
ngrok http 8000
```

If ngrok reports `Forwarding https://example.ngrok-free.app`, set:

```env
APP_BASE_URL=https://example.ngrok-free.app
```

The application then gives Twilio `https://example.ngrok-free.app/twilio/...` callbacks and `wss://example.ngrok-free.app/twilio/media`. Restart FastAPI after changing `.env`. Cloudflare Tunnel is also usable if it supplies one stable public HTTPS origin and supports WebSocket forwarding.

## Database initialization

No manual database creation is needed. During FastAPI startup, SQLAlchemy creates missing SQLite tables and applies the small compatibility column additions used by this v1. There is no Alembic command in this release. The default database file is `ai_phone_assistant.db` in the repository root; change `DATABASE_URL` before first startup to place it elsewhere.

## Start the application

```bash
uv run uvicorn app.main:app --reload
```

Browse to <http://localhost:8000>. `GET /health` checks SQLite and reports whether Twilio/OpenAI configuration appears present without calling either paid provider or returning credential values.

To explore the UI without paid calls, set `DRY_RUN=true`. A dry run creates a simulated call record, but no telephone rings and no Realtime conversation occurs.

## First call tutorial

Use your own verified mobile telephone for this canonical smoke test:

```text
Destination name: My mobile
Telephone number: +<country-code><number>
Objective: Ask me what time it is.
Context: This is a test call.
Preferences: Keep the call short.
Constraints: Do not perform any action other than asking the question.
Language: pt-PT
```

1. Confirm FastAPI and the tunnel are running and `DRY_RUN=false`.
2. Open the dashboard and select **New Call**.
3. Enter the values above; the number must be E.164, such as `+351` followed by the subscriber number.
4. Submit the form. Creation immediately requests the outbound call and opens its
   detail page.
5. Answer when the telephone rings. The agent should identify itself, ask the question, and converse briefly.
6. Hang up naturally or use **End Call**.
7. On the call page, inspect final transcript entries, facts, events, and the generated summary. Summary generation is asynchronous, so refresh/live updates may take a moment.
8. If recording was enabled, use the Audio/Recording section after Twilio marks it complete.

## Real-world informational example

```text
Destination name: Garden centre
Objective: Find out whether they currently have Echeveria plants.
Context: I may visit today.
Preferences: Ask which varieties they have and approximate sizes.
Constraints: Ask for prices. Do not reserve or purchase anything.
```

The agent may clarify varieties, sizes, prices, and availability. If offered a reservation, it must decline because the call did not authorize a commitment.

## Call authority model

```text
INFORMATION GATHERING = ALLOWED
COMMITMENTS = NOT ALLOWED unless explicitly authorized for that call
```

Allowed behavior includes asking about availability, prices, opening hours, requirements, follow-up details, and reference numbers. By default the agent may not book appointments, purchase products, reserve items, accept quotes, change contracts or services, or commit money. Agent instructions and the call configuration enforce this boundary. There is no owner-approval workflow.

## Recording

- `off`: never start a Twilio recording.
- `ask`: the agent asks the remote person first; recording starts only after clear consent and the `start_recording_after_consent` tool call.
- `always`: recording begins when the Media Stream starts, without an in-conversation consent tool step.

Recording metadata (SID, status, duration, channels, and timestamps) is stored in SQLite. The browser retrieves WAV through an authenticated server-side Twilio proxy endpoint; Twilio credentials and storage URLs are not exposed. By default audio remains at Twilio. With `DOWNLOAD_RECORDINGS_LOCALLY=true`, completed WAV data is also written beneath `RECORDINGS_DIR` and served from there when present.

The operator is responsible for configuring and using recording consistently with applicable law. The software does not determine whether recording is lawful.

## Transcript, recording, summary, events, and facts

- **Transcript:** final text from OpenAI Realtime input-transcription and assistant-output events. Partial deltas are not persisted as hundreds of rows.
- **Recording:** actual telephone audio, only when the recording policy starts it.
- **Summary:** structured post-call report generated with the Responses API from the transcript, facts, call objective, and tool outcomes.
- **Events:** meaningful operational timeline, not per-packet audio telemetry.
- **Facts:** important information the agent explicitly captured, including its confidence.

A transcript can exist without a recording, and a recording can complete after the telephone call ends.

## Troubleshooting and call correlation

Start with `GET /health`, the application log, the tunnel request log, Twilio **Monitor > Logs > Calls**, and the call detail event timeline. Common checks:

- **Phone never rings:** validate Twilio credentials, trial/geo restrictions, E.164 destination, caller number, and Twilio call logs.
- **Phone rings but is silent:** confirm `wss://.../twilio/media` connected, `OPENAI_API_KEY` is valid, and logs show `MEDIA_STREAM_STARTED` and `OPENAI_REALTIME_CONNECTED`.
- **WebSocket never connects:** check `APP_BASE_URL`, HTTPS/WSS tunnel support, returned TwiML, tunnel logs, and signature validation.
- **One-way audio:** input should produce Twilio `media` counters and OpenAI input events; output should produce Twilio JSON `media`/`mark` messages. Audio is PCMU/8 kHz/mono on both sides.
- **Slow turns or poor interruption:** inspect VAD settings and look for `ASSISTANT_INTERRUPTED`, Twilio `clear`, and mark handling before tuning values.
- **401/403 signatures:** make the externally signed callback URL and the proxy-visible URL identical; only disable validation for a controlled local diagnostic.
- **Incomplete transcript:** audio transport and transcription are separate; look for final transcription events and OpenAI errors.
- **Recording unavailable:** verify the policy, consent tool event in `ask` mode, recording callback, Twilio recording status, and credentials.

Each call has an internal UUID in the UI/database. Twilio adds a Call SID and Stream SID; recordings add a Recording SID. Use those identifiers with the call timeline and provider logs. The application does not log base64 audio. See the full [Troubleshooting guide](docs/TROUBLESHOOTING.md).

## Logging and privacy

`LOG_LEVEL=INFO` emits important call, stream, bridge, summary, and recording lifecycle events without one line per media packet. `LOG_LEVEL=DEBUG` adds protocol event types and playback/bridge diagnostics; it still does not intentionally log base64 audio payloads, API keys, authentication tokens, or entire transcripts. Do not enable third-party HTTP wire logging in production, and treat log files as sensitive operational data.

## HTTP and WebSocket reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Recent-call dashboard |
| `GET` | `/calls/new` | New-call form |
| `POST` | `/calls` | Create a call record (JSON API or browser form) |
| `GET` | `/calls/{id}` | Call detail page |
| `POST` | `/calls/{id}/end` | Idempotently end a live call |
| `GET` | `/calls/{id}/events` | Server-Sent Events live snapshot stream |
| `POST` | `/calls/{id}/summary/retry` | Retry a failed/missing summary |
| `GET` | `/calls/{id}/recording.wav` | Server-proxied completed recording |
| `GET` | `/health` | Local database/configuration health |
| `POST` | `/twilio/voice` | Signed Twilio answered-call webhook |
| `POST` | `/twilio/call-status` | Signed lifecycle callback |
| `POST` | `/twilio/recording-status` | Signed recording callback |
| `WS` | `/twilio/media` | Signed bidirectional Media Stream |

The owner-facing UI has no multi-user authentication in this v1; do not expose it directly to an untrusted network.

## Development and verification

```bash
uv sync
uv run ruff check .
uv run pytest
```

A real-provider manual checklist is in [Testing](docs/TESTING.md). Deeper design details are in [Architecture](docs/ARCHITECTURE.md).

## Current limitations

- Live WebSocket objects and active call orchestration are process-local; restarting the process ends an active conversation even though historical data remains durable.
- The schema initializer is appropriate for this v1 but is not a general migration system.
- The UI is designed for one trusted operator and has no account authentication or CSRF layer.
- Provider outages and delayed recording/summary callbacks remain visible as retryable failures rather than being hidden.
- Voicemail detection, automatic retries, calling-hour policy, and distributed multi-worker coordination are not implemented.
