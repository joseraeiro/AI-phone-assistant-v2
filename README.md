# Personal AI Telephone Agent

This repository will contain a Python personal AI telephone agent using Twilio
Voice and the OpenAI Realtime API.

Development is deliberately incremental. The target architecture, safety
boundaries, phase plan, and unresolved decisions live in
[`PROJECT_SPEC.md`](PROJECT_SPEC.md). Every implementation phase must read that
specification, inspect the existing code, run the existing tests, implement one
phase only, and rerun the tests.

The repository is currently at **Phase 9**. It creates an outbound Twilio call,
connects the answered call to a bidirectional Media Stream, and bridges telephone
audio to an interruptible, goal-directed OpenAI Realtime agent. Each call carries
its own objective, context, preferences, constraints, language, and explicit
authority grants. The agent remains non-binding by default and has only three
internal tools. Calls, canonical final transcripts, captured facts, and
meaningful lifecycle events are durable in SQLite. The application does not
record audio and does not implement approval or handoff. Completed calls receive
a structured post-call report generated through the OpenAI Responses API and
supports optional, policy-controlled Twilio call recording.

## Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A Twilio account with a voice-capable Twilio number
- An OpenAI API project with access to the configured Realtime model
- A publicly reachable HTTPS URL for Twilio webhooks

## Install and run

```bash
uv sync
cp .env.example .env
uv run uvicorn app.main:app --reload
```

Open `http://localhost:8000/` for the server-rendered dashboard. **New Call**
opens a form which creates and immediately starts the outbound call, then moves
to its detail page. The detail page receives status, canonical transcript,
fact, objective-status, and significant-event snapshots over Server-Sent
Events. It never receives raw audio. **End Call** requests a Twilio hangup and
is safe to press more than once.

The equivalent configured host and port command is:

```bash
uv run uvicorn app.main:app --host "$APP_HOST" --port "$APP_PORT"
```

Do not add provider credentials to source control. Put them in the ignored
`.env` file.

## Configuration

Set `APP_BASE_URL` to the public HTTPS origin that forwards to this application,
with no path or trailing slash. For example, after starting ngrok with
`ngrok http 8000`, use its HTTPS forwarding URL:

```dotenv
APP_BASE_URL=https://example-subdomain.ngrok-free.app
```

For Cloudflare Tunnel, forward the chosen public hostname to
`http://localhost:8000` and configure its HTTPS origin in the same way:

```dotenv
APP_BASE_URL=https://calls.example.com
```

Twilio signs the exact public webhook URL, so `APP_BASE_URL` must match the URL
Twilio requests. Keep `TWILIO_VALIDATE_SIGNATURES=true` for a real call. Setting
it to `false` is an explicit development-only bypass.

Configure the remaining values:

```dotenv
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+351...
TWILIO_VALIDATE_SIGNATURES=true
DRY_RUN=false

OPENAI_API_KEY=sk-...
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_VOICE=marin
OPENAI_REALTIME_VAD_TYPE=semantic_vad
OPENAI_REALTIME_VAD_EAGERNESS=auto
OPENAI_REALTIME_VAD_THRESHOLD=0.5
OPENAI_REALTIME_VAD_PREFIX_PADDING_MS=300
OPENAI_REALTIME_VAD_SILENCE_DURATION_MS=700
```

`semantic_vad` with `auto` eagerness is the telephone default: it uses semantic
completion rather than a short fixed silence and balances turn latency against
prematurely cutting off the caller. `low`, `medium`, or `high` eagerness can be
selected. If `OPENAI_REALTIME_VAD_TYPE=server_vad` is selected instead, the
threshold, prefix padding, and silence duration settings apply; the default
700 ms silence duration is intentionally not aggressive.

`TWILIO_PHONE_NUMBER` and the destination must use E.164 form. Trial Twilio
accounts may call only numbers permitted by Twilio's trial-account rules.

## Initiate a call

With the server and tunnel running:

```bash
curl -X POST http://localhost:8000/calls \
  -H 'Content-Type: application/json' \
  -d '{
    "destination_name": "Loja Exemplo",
    "destination_number": "+351...",
    "objective": "Confirmar a hora de fecho hoje e se abre amanhã de manhã.",
    "context": "A chamada é apenas para recolher informação.",
    "preferences": "Confirmar horários exatos.",
    "constraints": "Não fazer marcações nem assumir compromissos.",
    "language": "pt-PT",
    "authorized_actions": []
  }'
```

Twilio calls the destination and requests `POST /twilio/voice` after answer.
The returned TwiML connects the call to `WSS /twilio/media`, and the server opens
an authenticated server-to-server OpenAI Realtime WebSocket. The model identifies
itself as José's virtual assistant, briefly explains the call-specific objective,
and begins gathering information. It then uses configured server-side VAD for
subsequent turns. Lifecycle callbacks and packet/byte counters appear in logs.

Answer the call, listen for the introduction, say “Olá, estás a ouvir-me?”, and
continue for several turns. A successful stream produces logs with these
markers:

```text
MEDIA_STREAM_CONNECTED
MEDIA_STREAM_STARTED call_sid=CA... stream_sid=MZ... internal_call_id=...
OPENAI_REALTIME_CONNECTED model=gpt-realtime-2.1
MEDIA_RECEIVING call_sid=CA... stream_sid=MZ... packets=... bytes=... approx_seconds=...
ASSISTANT_INTERRUPTED item_id=item_... audio_end_ms=...
OPENAI_REALTIME_RESPONSE_CANCELLED
MEDIA_STREAM_STOPPED call_sid=CA... stream_sid=MZ... packets=... bytes=... approx_seconds=...
OPENAI_REALTIME_CLOSED
REALTIME_BRIDGE_STOPPED call_sid=CA... stream_sid=MZ...
```

The application never logs base64 payloads and does not record audio.

To validate locally without contacting Twilio, set `DRY_RUN=true`, restart the
server, and submit the same request. The response contains `"simulated": true`.

## Quality checks

```bash
uv run ruff check .
uv run pytest
```

## Twilio Media Stream protocol boundary

The implementation follows Twilio's current Media Streams message shapes for
`connected`, `start`, `media`, `dtmf`, `mark`, and `stop`. It expects the
documented fixed stream format: `audio/x-mulaw`, 8,000 Hz, mono. The start event
supplies the Twilio Call SID, Stream SID, media format, and custom parameters.
The media payload is base64-encoded audio. Unknown future event names are ignored
safely, while malformed known events are logged without including audio data.

Useful official references for integration review:

- [Media Streams WebSocket messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages)
- [`<Stream>` TwiML](https://www.twilio.com/docs/voice/twiml/stream)
- [Media Streams overview](https://www.twilio.com/docs/voice/media-streams)

## OpenAI Realtime protocol boundary

The default is the current `gpt-realtime-2.1` model and the recommended `marin`
voice. The backend authenticates a server-to-server WebSocket with
`OPENAI_API_KEY`, sends `session.update`, and requests audio-only output. Both
input and output are configured as `audio/pcmu`, the API's name for G.711
mu-law. This exactly matches Twilio's 8 kHz mono `audio/x-mulaw` payload, so the
codec boundary validates and forwards base64 chunks without transcoding.

Twilio input becomes `input_audio_buffer.append`. OpenAI audio is consumed only
from `response.output_audio.delta` and returned to Twilio as JSON `media`
messages using the active Stream SID. Each media chunk is followed by a uniquely
named Twilio `mark`; only returned marks count as played audio.

Semantic VAD is the default and emits `input_audio_buffer.speech_started` when
the caller barges in. With `interrupt_response=true`, OpenAI cancels the active
response. The bridge immediately suppresses late deltas, sends
`conversation.item.truncate` at the last mark-confirmed playback position, sends
Twilio `clear`, and resets local playback state. Marks returned after a clear are
stale and cannot advance the heard-audio position. This remains correct when an
interruption arrives just after model generation completes but while Twilio
still has buffered audio.

Official references reviewed for this phase:

- [Realtime API overview](https://developers.openai.com/api/docs/guides/realtime)
- [Realtime API with WebSocket](https://developers.openai.com/api/docs/guides/realtime-websocket)
- [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [Realtime VAD](https://developers.openai.com/api/docs/guides/realtime-vad)
- [`gpt-realtime-2.1` model](https://developers.openai.com/api/docs/models/gpt-realtime-2.1)

## Goal and authority policy

`POST /calls` requires `destination_name`, `destination_number`, `objective`,
`context`, `preferences`, and `constraints`; `language` defaults to `pt-PT`.
`authorized_actions` is an optional allow-list. An empty or missing allow-list
means the call has no authority to reserve, order, purchase, schedule, accept a
quote, commit money, cancel or modify a service, or enter an agreement. The
objective itself never grants that authority.

Owner fields are quoted as untrusted call data beneath an immutable authority
policy. The agent is instructed to clarify ambiguity, confirm prices, dates,
times and availability, save important facts, and politely decline unauthorized
commitments while continuing its informational objective. There is no approval
workflow.

The only model-visible internal tools are:

- `save_fact`: retain a categorized fact with confirmed, reported, or uncertain
  confidence;
- `set_objective_status`: set `success`, `partial`, `failed`, or `unknown` with
  a short operational reason;
- `finish_call`: schedule a brief spoken thank-you and goodbye. The bridge waits
  for Twilio's final playback mark before ending the stream.

Tool names are dispatched through a fixed allow-list and arguments are validated
with Pydantic. Their historical results are persisted; live tool/playback and
WebSocket state remains deliberately process-local.

The function-call event flow follows the current Realtime protocol: tools are
declared in `session.update`, completed calls are read from `response.done`,
results are returned as `function_call_output` conversation items, and a new
`response.create` continues the conversation.

- [OpenAI Realtime function calling](https://developers.openai.com/api/docs/guides/realtime-conversations#function-calling)

## Durable call history

`DATABASE_URL` defaults to `sqlite+aiosqlite:///./ai_phone_assistant.db`. The
application creates the SQLAlchemy schema at startup and stores call
configuration/state, final remote and assistant transcripts, captured facts,
and significant provider/agent events. All stored timestamps are UTC;
`APP_TIMEZONE=Europe/Lisbon` reserves the display timezone for a later UI.

Remote transcript deltas are not stored. Canonical remote entries come from
`conversation.item.input_audio_transcription.completed`; canonical assistant
entries come from `response.output_audio_transcript.done`. Item sequencing is
assigned when conversation items are observed, so completion events arriving
out of order do not reorder the conversation. Audio packets and live WebSocket
objects are never written to SQLite.

- [OpenAI Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription)

## Web interface

The interface uses Jinja2 templates, plain CSS, and small vanilla JavaScript
modules under `app/templates` and `app/static`. There is no browser build step.
Jinja autoescaping and DOM `textContent` protect provider/model text when it is
rendered. Provider credentials and authenticated media never enter page data.

The dashboard lists recent calls and historical detail pages remain available
after restart because they read SQLite rather than the process-local call
store.

## Post-call reports

After Twilio reports a call as completed, the application submits the objective,
call configuration, canonical transcript, captured facts, objective state, and
important tool outcomes to the OpenAI Responses API. `OPENAI_SUMMARY_MODEL`
defaults to `gpt-5.6-luna`, the current efficient high-volume GPT-5.6 variant;
it is intentionally separate from the Realtime voice model.

The Responses SDK parses directly into a strict Pydantic schema. Information
and important numbers carry `confirmed`, `uncertain`, or `not_obtained`
certainty, and the instructions prohibit inventing evidence or upgrading
uncertainty. Commitments must remain empty unless both explicit authority and
call evidence establish one.

Generation is atomically claimed in SQLite. Duplicate completion callbacks do
not make duplicate model requests. A successful report stores structured JSON,
display text, and its UTC generation time. A failure leaves the call completed
and its transcript intact, records a separate report error, and exposes a retry
button on the call page.

Official implementation references:

- [Responses API text generation](https://developers.openai.com/api/docs/guides/text)
- [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Current model guidance](https://developers.openai.com/api/docs/guides/latest-model)

## Optional call recording

`DEFAULT_RECORDING_POLICY` accepts `off`, `ask`, or `always` and defaults to
`ask`. A call may override that default in the API or new-call form. Recording
is independent of Realtime transcription and post-call reports.

- `off`: no recording is started and the agent does not ask about recording.
- `ask`: the agent asks naturally before pursuing the objective. Only a clear
  agreement permits the allowlisted `start_recording_after_consent` tool. A
  refusal or unclear answer starts no recording and the conversation continues.
- `always`: the backend starts recording when Twilio establishes the Media
  Stream; the agent does not request consent.

The application makes no legal determination about recording. **The operator is
responsible for selecting and operating a policy consistent with all applicable
laws, consent requirements, and notices.** There is no owner approval workflow.

The live-call Recording API requests both tracks in dual-channel format and
uses an idempotent signed status callback. Metadata is durable in SQLite. The
call page uses an internal WAV endpoint: the server authenticates to Twilio and
never exposes credentials or a provider media URL to the browser. Dual-channel
retrieval automatically falls back to mono when Twilio reports that dual media
is unavailable.

By default, media remains at Twilio and is proxied on demand. Set
`DOWNLOAD_RECORDINGS_LOCALLY=true` to download completed WAV files into
`RECORDINGS_DIR` (default `./data/recordings`). Local media files remain runtime
data and must not be committed.

- [Twilio Recordings resource and live-call API](https://www.twilio.com/docs/voice/api/recording)
