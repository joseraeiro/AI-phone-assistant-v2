# Architecture

## System boundaries

```text
trusted browser ──HTTP/SSE──> FastAPI ──async SQLAlchemy──> SQLite
                                │
                                ├──Twilio REST──> outbound PSTN call
                                │                    │
                                │<──signed webhooks──┤
                                │<══Twilio WSS Media Stream══> telephone
                                │
                                ├══OpenAI Realtime WebSocket══> speech session
                                └──OpenAI Responses API───────> structured summary
```

FastAPI is the only component that holds Twilio and OpenAI credentials. The browser receives rendered history and structured live snapshots, never provider credentials or raw audio.

## Call lifecycle

1. `POST /calls` validates an E.164 destination and persists the call configuration in `CREATED` state.
2. The same `POST /calls` operation asks Twilio to create the outbound call. The REST request supplies the answered-call and lifecycle callback URLs dynamically.
3. Twilio posts lifecycle states to `/twilio/call-status`. Meaningful transitions are persisted idempotently.
4. On answer, `/twilio/voice` returns `<Connect><Stream>` TwiML. The internal UUID is a custom Stream parameter; it is not a secret.
5. Twilio opens `/twilio/media`. The start event correlates internal UUID, Call SID, Stream SID, and media format.
6. `RealtimeAudioBridge` connects and configures OpenAI, starts the agent's first response, and relays both directions concurrently.
7. Final input and output transcripts, facts, tool outcomes, and significant events are written to SQLite. Audio packets are counted but not persisted or logged.
8. Hangup/stop closes the bridge. Completion triggers an idempotent post-call summary attempt; a failure leaves call history intact and can be retried.

## Audio and turn taking

Twilio declares `audio/x-mulaw`, 8 kHz, one channel. The codec boundary verifies this as PCMU and maps it to OpenAI's G.711 μ-law configuration. Audio remains base64 within provider JSON messages and is forwarded without decoding/re-encoding or transcoding.

Twilio input `media.payload` becomes an OpenAI `input_audio_buffer.append`. OpenAI audio deltas become Twilio JSON `media` messages using the active Stream SID. Marks track queued assistant audio playback without assuming that generated audio was heard.

OpenAI server/semantic VAD reports remote speech start. If assistant audio is pending, the bridge cancels the active response, truncates the assistant conversation item at the played position, sends Twilio `clear`, and resets local playback state. Generation identifiers prevent stale deltas from a cancelled response leaking into a later turn. A `TaskGroup` ties relay task lifetimes together so either-side disconnect tears down the session.

## Agent and tools

`build_agent_instructions(call)` is the single prompt-construction boundary. It includes call-specific objective/context/preferences/constraints, truthful virtual-assistant identity, concise pt-PT telephone style, and the non-binding authority policy.

Only allowlisted, Pydantic-validated tools are dispatched:

- `save_fact`
- `set_objective_status`
- `finish_call`
- `start_recording_after_consent`

Unknown function names and invalid arguments cannot execute arbitrary Python. `finish_call` requests termination only after final speech playback has completed.

## Persistent and live state

SQLite is authoritative for historical calls. The principal entities are:

- `Call`: configuration, provider IDs, lifecycle timestamps/statuses, summary fields, and errors;
- `TranscriptEntry`: ordered final agent/remote/system/tool text;
- `CallEvent`: significant operational events and deduplication keys;
- `CapturedFact`: explicit facts and confidence;
- `Recording`: Twilio recording status and metadata.

The process-local `CallStore` holds active call configuration used to initialize a live WebSocket session. WebSocket objects, asyncio tasks, partial transcript deltas, and playback queues are never stored in SQLite. This means historical state survives restart, but an in-flight call does not.

Startup calls SQLAlchemy `create_all` and applies narrowly scoped compatibility column additions. No Alembic migration framework is included.

## Recording lifecycle

`off` never records. `ask` exposes the consent tool and starts Twilio live recording only after the model reports clear consent. `always` starts recording once the Twilio Stream is established. The Twilio request asks for dual-channel/both-track recording and supplies a signed status callback.

Callbacks upsert recording metadata by Recording SID, so duplicates are idempotent. With local download disabled, audio remains on Twilio and `/calls/{id}/recording.wav` retrieves it server-to-server with Twilio authentication. With local download enabled, the completed WAV is stored beneath the configured private directory and served by that same endpoint.

## Summarization

After completion, `PostCallSummaryService` sends objective, context, preferences, constraints, final transcript, facts, objective status, and relevant tool events to the OpenAI Responses API. The result must validate against a Pydantic structured schema. A claim-level confidence rule asks the model to preserve confirmed, uncertain, and not-obtained distinctions. An existing successful summary is returned without another provider request; failures are stored separately and retryable.

## Security and observability

Twilio HTTP and WebSocket entry points use the official SDK `RequestValidator` when signature checks are enabled. Secrets never enter Stream URLs or templates. Normal logs name lifecycle events and identifiers but exclude base64 audio payloads and credentials. SQLite databases, recordings, `.env`, caches, and common audio exports are ignored by Git.

Identifiers serve different diagnostic scopes:

- internal UUID: dashboard, SQLite, all application events;
- Call SID: Twilio call creation and Voice logs;
- Stream SID: Media Stream connection and outbound media/mark messages;
- Recording SID: Twilio recording lifecycle and retrieval.

The current adapter does not retain an OpenAI session identifier; correlate Realtime failures by internal call UUID, adjacent timestamps, and bridge events.
