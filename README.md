# Personal AI Telephone Agent

This repository will contain a Python personal AI telephone agent using Twilio
Voice and the OpenAI Realtime API.

Development is deliberately incremental. The target architecture, safety
boundaries, phase plan, and unresolved decisions live in
[`PROJECT_SPEC.md`](PROJECT_SPEC.md). Every implementation phase must read that
specification, inspect the existing code, run the existing tests, implement one
phase only, and rerun the tests.

The repository is currently at **Phase 4**. It creates an outbound Twilio call,
connects the answered call to a bidirectional Media Stream, and bridges telephone
audio to an interruptible OpenAI Realtime speech-to-speech session. Semantic VAD
detects natural turns, while Twilio marks, buffer clearing, and Realtime
conversation truncation implement barge-in without treating cleared audio as
heard. The application does not record or persist audio and does not implement
objectives, tools, summaries, approval, handoff, or a frontend.

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
  -d '{"destination_number":"+351..."}'
```

Twilio calls the destination and requests `POST /twilio/voice` after answer.
The returned TwiML connects the call to `WSS /twilio/media`, and the server opens
an authenticated server-to-server OpenAI Realtime WebSocket. The model is asked
to say “Boa tarde. Sou o assistente virtual do José.” as its first utterance and
then uses configured server-side VAD for subsequent turns. Lifecycle callbacks
and periodic packet/byte counters appear in logs.

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
