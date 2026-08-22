# Troubleshooting

Use `LOG_LEVEL=INFO` first. For one failing call, record the internal call UUID from the URL, Twilio Call SID, Stream SID, and Recording SID where applicable. Compare application timestamps with the tunnel request log and Twilio Console call/recording logs. `DEBUG` adds event-type and playback diagnostics without intentionally logging audio payloads or secrets.

## Phone never rings

1. Check `GET /health`: `twilio_configured` should be `true` and `dry_run` should be `false`.
2. Confirm the destination and `TWILIO_PHONE_NUMBER` use E.164 (`+` plus country code and digits).
3. Check the browser/API error and Twilio **Monitor > Logs > Calls** for permission, geo, balance, caller-ID, or destination failures.
4. On a trial account, confirm the destination is verified.
5. Find `CALL_REQUESTED`/`CALL_FAILED` in the persisted event timeline. No Call SID usually means Twilio rejected REST creation before ringing.

## Phone rings but the agent is silent

- Confirm Twilio requested `/twilio/voice` and its response contains `<Connect><Stream>` with the expected `wss://` host.
- Look for `MEDIA_STREAM_CONNECTED`, `MEDIA_STREAM_STARTED`, and `OPENAI_REALTIME_CONNECTED` in order.
- Validate the OpenAI key/model access and inspect `OPENAI_REALTIME_CONFIGURATION_ERROR` or `OPENAI_REALTIME_ERROR`.
- Verify the Twilio start format is μ-law/8 kHz/mono. A format mismatch is rejected rather than silently transcoded.

## Twilio WebSocket never connects

- `APP_BASE_URL` must be the current public origin, not localhost and not an old tunnel URL. Restart after editing `.env`.
- The tunnel must support WebSocket upgrades and forward to the same FastAPI port.
- Inspect tunnel requests for `/twilio/voice` and `/twilio/media` and inspect Twilio's debugger.
- Ensure no path was included in `APP_BASE_URL`; the application appends `/twilio/...`.
- If the server rejects before accepting, compare the `X-Twilio-Signature` against the exact public WSS URL. A reverse proxy rewriting scheme, host, or query can invalidate it.

## The agent cannot hear the remote person

This is the Twilio-to-OpenAI path. Look for increasing media counters (`MEDIA_RECEIVING` is sampled, not emitted for every packet), a valid start format, and an active OpenAI socket. Twilio input media should become `input_audio_buffer.append`. If the agent is audible, the output half works and diagnosis should focus on incoming `media` events, track selection, format validation, and OpenAI input/VAD events such as `REMOTE_SPEECH_STARTED`.

## The remote person cannot hear the agent

This is the OpenAI-to-Twilio path. Confirm OpenAI emits output-audio deltas, the bridge has the active Stream SID, and Twilio receives JSON `media` messages (not binary frames). Marks should be queued after audio. Look for `REALTIME_BRIDGE_OPENAI_RECEIVE_FAILED`, Twilio disconnects, or stale-generation drops. Do not decode/re-encode PCMU while diagnosing: both provider sessions are configured for G.711 μ-law.

## Conversation has large latency

- Compare `REMOTE_SPEECH_STARTED`, final input transcript, `AGENT_RESPONSE_STARTED`, and first playback mark timestamps.
- Avoid tuning several VAD values simultaneously. `semantic_vad` with eagerness `auto` is the default. For `server_vad`, silence duration controls how long the system waits after speech.
- High network latency to either provider, tunnel buffering, and long agent utterances also add delay.
- Confirm the event loop is not running blocking custom code and media reception remains continuous.

## The assistant does not stop when interrupted

A working interruption produces remote speech-start detection, response cancellation/truncation, a Twilio `clear`, and playback reset. Look for `ASSISTANT_INTERRUPTED`; then inspect response/item identifiers and Twilio clear/mark events at DEBUG. `OPENAI_REALTIME_STALE_AUDIO_DROPPED` is expected if a cancelled generation emits late deltas. If speech start never arrives, diagnose VAD/input audio before playback logic. If it arrives but old audio continues, diagnose the Twilio `clear` message and active Stream SID.

## OpenAI connection fails

`OPENAI_REALTIME_CONNECTED` should follow Stream start. Check `OPENAI_API_KEY`, configured model/voice/transcription model access, outbound network access, and `OPENAI_REALTIME_CONFIGURATION_ERROR`/`OPENAI_REALTIME_ERROR`. The application closes both relay directions when OpenAI disconnects to avoid zombie tasks. It does not expose the raw provider exception in the browser; use the server log.

## Twilio returns 401/403 or signature validation fails

The official Twilio SDK validator uses the externally visible callback URL and form/query parameters. Set `APP_BASE_URL` to the exact HTTPS origin Twilio uses. Check proxy forwarded scheme/host and avoid changing query strings. Keep `TWILIO_AUTH_TOKEN` synchronized with the project that owns the Call SID. `TWILIO_VALIDATE_SIGNATURES=false` is only a controlled-development diagnostic; do not use it as a production fix.

## Transcript is incomplete

Audio forwarding and transcription are separate. The database persists final OpenAI transcript-completion events, not partial deltas. Check OpenAI transcription errors/disconnect timing, speaker-specific final events, and whether hangup occurred before finalization. A Twilio recording, when enabled, does not backfill transcript text automatically.

## Summary is absent or failed

The telephone call can remain completed even if the Responses API fails or returns invalid structured output. Check `summary_error`, `POST_CALL_SUMMARY_FAILED`, the configured summary model/key, and the final transcript. Use **Retry summary** once the cause is fixed. Completion callback duplication does not intentionally create multiple successful summaries.

## Recording is unavailable

- Confirm the effective policy. `off` is deliberately unavailable; `ask` requires clear consent and a completed consent tool call; `always` starts at Stream start.
- Find the Recording SID and status callback event. Twilio may finalize audio after call completion.
- For proxied retrieval, the recording must be complete and Twilio credentials must still authorize it.
- For local storage, verify `DOWNLOAD_RECORDINGS_LOCALLY=true`, `RECORDINGS_DIR` is writable, and inspect `TWILIO_RECORDING_DOWNLOAD_FAILED`.
- `TWILIO_RECORDING_START_FAILED` indicates the live Recording API request failed; transcript operation can continue independently.

## Database or startup problems

`GET /health` performs `SELECT 1` against the configured database. Confirm the parent directory exists/is writable and the URL begins `sqlite+aiosqlite://` for SQLite. Startup creates tables automatically. An invalid `APP_BASE_URL` fails settings validation; missing live-provider variables produce `LIVE_CONFIGURATION_INCOMPLETE` with variable names only. Set `DRY_RUN=true` for UI/database testing without paid-provider credentials.

## Identifier correlation

| Identifier | Where to find it | Use |
|---|---|---|
| Internal call UUID | page URL, `Call.id`, custom Stream parameter | join all application history |
| Twilio Call SID | call detail, Twilio call log | PSTN and lifecycle diagnosis |
| Twilio Stream SID | call detail/events and Twilio Stream messages | Media Stream diagnosis |
| Recording SID | Audio section/recording row, Twilio recordings | callback and WAV retrieval |

The current implementation does not persist the OpenAI Realtime session identifier. Correlate OpenAI events with internal call UUID, Stream SID, and adjacent timestamps. This avoids enabling packet-level logging, which is both noisy and sensitive.
