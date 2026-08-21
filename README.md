# Personal AI Telephone Agent

This repository will contain a Python personal AI telephone agent using Twilio
Voice and the OpenAI Realtime API.

Development is deliberately incremental. The target architecture, safety
boundaries, phase plan, and unresolved decisions live in
[`PROJECT_SPEC.md`](PROJECT_SPEC.md). Every implementation phase must read that
specification, inspect the existing code, run the existing tests, implement one
phase only, and rerun the tests.

The repository is currently at **Phase 1**. It can create one outbound Twilio
call, speak a fixed Portuguese test message, and log Twilio lifecycle callbacks.
It does not yet implement OpenAI Realtime, Media Streams, audio processing,
transcription, recording, summarization, tools, persistence, or a frontend.

## Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A Twilio account with a voice-capable Twilio number
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
```

`TWILIO_PHONE_NUMBER` and the destination must use E.164 form. Trial Twilio
accounts may call only numbers permitted by Twilio's trial-account rules.

## Initiate a call

With the server and tunnel running:

```bash
curl -X POST http://localhost:8000/calls \
  -H 'Content-Type: application/json' \
  -d '{"destination_number":"+351..."}'
```

Twilio calls the destination, requests `POST /twilio/voice` after answer, plays
“Olá. Esta é uma chamada de teste do assistente virtual.”, and ends the call.
Lifecycle callbacks are sent to `POST /twilio/call-status` and appear in the
server logs with the call SID and status.

To validate locally without contacting Twilio, set `DRY_RUN=true`, restart the
server, and submit the same request. The response contains `"simulated": true`.

## Quality checks

```bash
uv run ruff check .
uv run pytest
```
