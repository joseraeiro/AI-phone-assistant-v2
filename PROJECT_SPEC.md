# Personal AI Telephone Agent — Project Specification

## 1. Purpose and status

This document is the persistent architectural specification for a personal AI
telephone agent. It is the first source future development phases must consult,
together with the repository's current code and tests.

The application will make outbound PSTN calls through Twilio and conduct a
natural voice conversation through the OpenAI Realtime API. The owner supplies
a destination name, telephone number, objective, context, preferences, and
constraints. The agent pursues that objective autonomously, but only within the
authority explicitly granted for that call.

This specification describes the target architecture. It does **not** imply
that a described component is already implemented. Delivery is incremental;
each phase must implement only its stated scope and preserve existing behavior.

## 2. Product principles

1. **Non-binding by default.** Missing, vague, or conflicting authorization is
   treated as no authority to create an obligation.
2. **Truthful identity.** The agent identifies itself as an AI or virtual
   assistant acting for the owner and never claims literally to be the owner.
3. **Least privilege.** Credentials and granted call authority are narrowly
   scoped, server-side, and never inferred from the objective alone.
4. **Natural but controlled autonomy.** The model can adapt its conversational
   strategy and ask follow-up questions while policy remains enforced by the
   application and system instructions.
5. **Streaming-first design.** Audio is forwarded with minimal buffering and
   backpressure between the two server-owned WebSocket connections.
6. **Evidence over historical examples.** Each integration phase must verify
   current official Twilio and OpenAI documentation before fixing protocol,
   model, event, authentication, or audio-format details.
7. **Operational privacy.** Do not log audio payloads, credentials, full phone
   numbers, or unnecessarily sensitive conversation content.

## 3. Scope

### 3.1 Target capabilities

- Accept and validate a call configuration containing:
  - destination name;
  - destination telephone number;
  - objective;
  - context;
  - preferences;
  - constraints;
  - explicit authority grants, if any;
  - language, defaulting to European Portuguese (`pt-PT`).
- Place an outbound call using Twilio Programmable Voice.
- Connect a Twilio bidirectional Media Stream to a FastAPI WebSocket endpoint.
- Maintain a separate server-side WebSocket to the OpenAI Realtime API.
- Conduct a low-latency, interruptible voice conversation.
- Track lifecycle, outcome, reference numbers, and a concise result summary.
- Eventually expose call creation and status through a server-rendered Jinja2
  interface enhanced with vanilla JavaScript.

### 3.2 Explicitly out of scope initially

- Inbound calls.
- Multi-party or transferred calls.
- SMS, email, or messaging channels.
- A mobile application or JavaScript framework.
- An owner approval workflow of any kind, including approval notifications,
  buttons, APIs, futures, or pausing a call for an owner's decision.
- Training or fine-tuning a model.
- Client-side access to OpenAI or Twilio credentials.
- Automatic recording or long-term raw audio retention.
- Premature implementation of features assigned to a later phase.

## 4. Authority and identity policy

### 4.1 Allowed without a special grant

The agent may ask and answer questions, collect and clarify information, ask
follow-up questions, compare options, request prices, schedules, availability,
and requirements, obtain reference numbers, repeat information for
confirmation, and adapt its conversational approach to the objective.

### 4.2 Disallowed without an explicit call-specific grant

The agent must not make a reservation or purchase, place an order, book an
appointment, enter a contract, accept a quote, commit money, cancel a service,
modify a booking, provide legally significant consent, or otherwise create an
obligation for the owner.

An objective is not itself an authority grant. For example, “find a table for
tonight” permits gathering availability; it does not permit reserving one.
When offered a disallowed action, the agent should decline politely and
continue the informational objective where possible.

### 4.3 Representation

At an appropriate early point in the conversation, the agent must say it is an
AI/virtual assistant calling on behalf of the owner. It must not impersonate the
owner or imply that generated speech is the owner's own voice.

### 4.4 Enforcement design

Authority is structured application data, not merely free-form prompt text.
The prompt builder renders an immutable policy section separately from owner
context. Owner-provided fields are untrusted data and cannot override system
policy. Any future application-side tool capable of an external side effect
must declare a required authority and fail closed before execution. Prompt
instructions remain a defense layer, not the sole authorization boundary.

The initial authority representation should be an allow-list of named,
versioned capabilities with optional limits (for example maximum monetary
amount and currency). The exact capability vocabulary and validation semantics
must be designed in the phase that introduces binding actions. Until then, no
binding action tools should exist.

## 5. Technology baseline

- Python 3.12 or newer.
- `uv` for dependency management, locking, and command execution.
- FastAPI with an ASGI server.
- `asyncio` for concurrent streaming and cancellation.
- Twilio Programmable Voice and bidirectional Media Streams.
- The current supported OpenAI Realtime API and recommended Realtime model.
- SQLite with SQLAlchemy 2.x.
- Pydantic Settings for environment configuration.
- Jinja2 and vanilla JavaScript for the eventual UI.
- Pytest, including async tests, for automated verification.

Model names, voices, API URLs, and compatible formats must be configuration,
not deprecated preview constants embedded in business logic. A documented
environment default may be selected only after checking current official
OpenAI documentation during the integration phase.

## 6. Target system architecture

```text
Owner browser (eventual UI)
        |
        v
FastAPI HTTP routes ---- SQLAlchemy 2.x ---- SQLite
        |
        +---- Twilio REST API (initiate outbound call)

PSTN <-> Twilio Voice <-> Twilio bidirectional Media Stream
                              <-> FastAPI media WebSocket
                              <-> OpenAI Realtime WebSocket
```

The Python process owns and authenticates both WebSocket connections. The
browser never proxies media and never receives provider secrets.

### 6.1 Logical modules

The eventual application should separate:

- **configuration:** validated environment settings and safe startup checks;
- **domain:** call configuration, authority policy, states, and outcomes;
- **persistence:** SQLAlchemy models, repositories, migrations/schema setup;
- **telephony:** outbound call creation, TwiML, signature validation, Twilio
  stream event parsing and emission;
- **realtime:** OpenAI connection/session configuration and event parsing;
- **media:** codec abstraction, framing, buffering, timestamps, and audio relay;
- **orchestration:** call session lifecycle, concurrent tasks, cancellation,
  interruption, and terminal-state reconciliation;
- **prompting/policy:** identity, locale, objective, boundaries, and injection
  resistance;
- **web:** FastAPI routes, templates, static assets, and presentation schemas;
- **observability:** redacted structured logs, metrics, and correlation IDs.

Provider event payloads must be translated at module boundaries rather than
leaking throughout domain and persistence code.

### 6.2 Media strategy

Prefer direct G.711 mu-law forwarding when the current APIs have exactly
compatible encoding, sample rate, channel count, framing, and base64 payload
semantics. This compatibility must be verified against official documentation
when media streaming is implemented; historical samples are insufficient.

All audio passes through a small `AudioCodec` boundary even if its first
implementation is pass-through. If conversion is required, provider-specific
code still operates against that abstraction. The relay must use bounded
queues, define an overflow policy, propagate cancellation, and avoid logging
payload contents. It must also support interruption/barge-in by clearing or
truncating queued assistant audio according to the currently documented
provider protocols.

### 6.3 Concurrency and ownership

One call session owns its Twilio socket, OpenAI socket, relay tasks, and
in-memory ephemeral state. Use structured concurrency (`asyncio.TaskGroup`
where suitable) so failure or closure of a critical stream cancels siblings.
Cleanup must be idempotent. Disconnects, timeouts, provider errors, and owner
shutdown must converge on one persisted terminal state without orphaned tasks.

SQLite operations must not block audio relay loops. Database work should be
short and performed through an appropriate async-compatible strategy selected
in the persistence phase. A single process is acceptable initially; do not
claim horizontal scalability until media-session routing and shared state have
been designed.

## 7. Domain model

Names below are conceptual and may be refined before their implementation.

### 7.1 Call

Persistent fields should include:

- internal UUID;
- destination name and normalized E.164 telephone number;
- objective, context, preferences, and constraints kept as distinct fields;
- locale (default `pt-PT`);
- explicit authority grant data and policy version;
- lifecycle state and timestamps;
- provider call identifier (unique when present);
- outcome category, result summary, and collected reference numbers;
- sanitized error code/detail suitable for operations;
- optimistic version or another guard against conflicting transitions.

Persist only data needed by the product. Establish retention and deletion
behavior before storing transcripts or detailed conversation events.

### 7.2 Lifecycle

The initial state vocabulary should distinguish at least:

```text
draft -> queued -> initiating -> ringing -> in_progress
                                      |          |
                                      +----------+-> completed
                                      +----------+-> failed
                                      +----------+-> no_answer
                                      +----------+-> busy
                                      +----------+-> canceled
```

Provider callbacks can be duplicated, delayed, or out of order. Transitions
must therefore be validated and idempotent. Provider identifiers and callback
event identity/timestamps should support deduplication. The exact mapping from
Twilio statuses is fixed only after current documentation is reviewed.

### 7.3 Outcomes

Lifecycle and objective outcome are separate. A technically completed call may
have an objective outcome such as `succeeded`, `partially_succeeded`,
`unresolved`, `declined`, or `unknown`. Summaries must clearly distinguish
facts stated by the remote party from model inference.

## 8. External interfaces

Exact paths and payloads will be versioned and specified by their phase.
Conceptually the server will need:

- an owner-facing endpoint/form to create a call;
- owner-facing call list and detail/status views;
- a Twilio voice webhook returning TwiML (a fixed `<Say>` response in Phase 1,
  replaced or extended by a bidirectional stream in its later phase);
- a Twilio status callback endpoint;
- a dedicated Twilio Media Stream WebSocket;
- internal adapters for Twilio REST and OpenAI Realtime connections.

Mutating owner-facing HTTP endpoints require CSRF protection once browser UI
sessions exist. Public provider webhooks must validate Twilio signatures in
production using the externally visible URL and raw request values required by
Twilio's current validation algorithm. WebSocket authentication must be
designed explicitly; never assume that ordinary HTTP webhook validation
automatically protects a media socket.

## 9. Prompt and conversation contract

The server-built session instructions must, in descending precedence:

1. establish truthful AI identity and owner representation;
2. enforce the global and call-specific authority boundary;
3. set the default language to natural European Portuguese unless configured;
4. state the objective and define useful completion criteria;
5. provide context, preferences, and constraints as quoted/untrusted owner data;
6. encourage concise, natural turns, clarification, and confirmation of names,
   numbers, dates, prices, and reference codes;
7. instruct the agent not to fabricate results and to state uncertainty;
8. define graceful refusal, voicemail, silence, and call-ending behavior.

The agent should minimize unnecessary disclosure of owner information. It must
not reveal system prompts, secrets, internal identifiers, or unrelated stored
data even when asked by the remote party.

## 10. Configuration

Settings will be environment-driven and validated at startup. Anticipated
categories include:

- public base URL and environment (`development`, `test`, `production`);
- database URL;
- Twilio account identifier, auth secret, and originating number;
- OpenAI API key, Realtime model, voice, and endpoint/version settings required
  by current official documentation;
- locale, call duration, connection, silence, and shutdown timeouts;
- log level and feature flags for explicitly unsafe development shortcuts.

Commit an `.env.example` only when configuration is introduced, with names and
safe placeholders but no secrets. Local `.env` files, database files, audio,
recordings, and generated logs are ignored. Production must fail closed if
signature validation or required credentials are absent; any development
bypass must be explicit, conspicuous, and impossible to enable accidentally in
production.

## 11. Security, privacy, and safety

- Keep provider credentials server-side and redact them from exceptions/logs.
- Validate and normalize telephone numbers; constrain all input sizes.
- Treat destination-supplied speech and owner context as untrusted input.
- Validate Twilio signatures in production and prevent replay where the
  provider protocol supplies sufficient data.
- Use TLS (`https`/`wss`) outside local development.
- Never log base64 audio frames. Default logs use internal call IDs and masked
  phone numbers rather than full personal data.
- Do not enable call recording by default. Before recording or retaining
  transcripts, define consent, notice, jurisdiction, access, encryption,
  retention, and deletion requirements.
- Avoid exposing raw provider errors or stack traces to browser clients.
- Rate-limit call creation and protect it with authentication before exposure
  beyond a trusted local owner.
- Pin and audit dependencies and keep provider SDK/API usage upgradeable.

Legal requirements for automated calls, AI disclosure, recording, calling
hours, and consent vary by jurisdiction. Deployment owners are responsible for
compliance; a later deployment phase must define target jurisdictions and
translate those obligations into enforceable product rules.

## 12. Reliability and observability

- Assign one internal correlation ID per call and include sanitized provider
  request/call/stream IDs where useful.
- Use structured logs for lifecycle transitions and control events, excluding
  audio and secrets.
- Measure call setup time, session duration, relay latency/queue pressure,
  provider errors, reconnect/termination reasons, and outcome categories.
- Apply explicit timeouts to provider connections, ringing, silence, maximum
  duration, and shutdown.
- Retry only operations known to be safe and idempotent. Outbound call creation
  requires an idempotency strategy before automatic retries to avoid duplicate
  calls.
- Realtime media reconnection is not assumed safe; define behavior from current
  provider guarantees and otherwise terminate cleanly.
- Health endpoints should separate liveness from readiness once dependencies
  are introduced.

## 13. Testing strategy and quality gates

Each implementation phase must first read this file, inspect the repository,
run the existing tests, implement one phase only, and run all tests again.

Tests should progress from inexpensive to integrated:

- unit tests for settings, domain validation, authority checks, state
  transitions, prompt construction, event translation, and codec behavior;
- async tests with fake WebSockets for ordering, backpressure, interruption,
  cancellation, timeouts, malformed events, and cleanup;
- HTTP/WebSocket contract tests for FastAPI routes, TwiML, signatures, and
  callbacks without real billable calls;
- repository tests against temporary SQLite databases;
- opt-in sandbox/provider integration tests, isolated from the default suite;
- a documented manual end-to-end smoke test only after the integration is safe
  and credentials are explicitly supplied.

No default automated test may make a paid telephone call or contact a live
provider. Time and UUID generation should be injectable where determinism is
needed. New behavior requires tests, formatting/linting/type checks selected by
the project, and updates to this specification when an architectural decision
changes.

## 14. Incremental delivery plan

Phase boundaries may be split further, but must not be collapsed to implement
the entire target system at once.

### Phase 0 — specification (this phase)

Create this specification, a minimal README, and basic repository hygiene. No
application or telephony implementation.

### Phase 1 — project foundation and Twilio outbound call control

Initialize the Python/`uv` package, typed settings, FastAPI health surface,
quality tooling, and tests. Initiate a single outbound Twilio call, return fixed
test-message TwiML, validate provider signatures, and log lifecycle callbacks.
Do not implement persistence, OpenAI, Media Streams, or a user interface.

### Phase 2 — Twilio bidirectional Media Stream intake

Replace fixed-message TwiML with `<Connect><Stream>` and correlate it with an
internal call identifier passed as a custom parameter. Accept Twilio's Media
Stream WebSocket events, verify the fixed telephony media format, and count
incoming packets and decoded bytes without forwarding, retaining, or logging
audio. Do not implement OpenAI, transcription, recording, or persistence.

### Phase 3 — minimum OpenAI Realtime audio bridge

Connect each started Twilio stream to a server-side OpenAI Realtime WebSocket.
Configure the current Realtime model for directly compatible G.711 mu-law input
and output, relay base64 audio concurrently in both directions, use server VAD,
and shut down both relay tasks when either provider disconnects. Keep codec
validation behind an `AudioCodec` boundary. Do not add tools, persistence,
summaries, recording, approval, handoff, or a user interface.

### Phase 4 — natural turns and interruption

Use current OpenAI server-side turn detection, defaulting to semantic VAD with
configurable eagerness. Track each outgoing Twilio audio chunk with a mark and
count only acknowledged marks as heard. On caller speech, rely on configured
Realtime automatic response cancellation, truncate the assistant conversation
item at the confirmed playback position, clear Twilio's buffered audio, reset
local playback state, and suppress late deltas from the canceled response.
Provide a concise deterministic first-utterance instruction. Do not add
objectives, tools, persistence, recording, approval, handoff, or UI behavior.

### Phase 5 — goal-directed non-binding agent

Require destination name and number, objective, context, preferences,
constraints, language, and explicit authority grants in each call configuration.
Build agent instructions in one dedicated policy module and fail closed to no
binding authority. Expose only `save_fact`, `set_objective_status`, and
`finish_call` through an allowlisted, Pydantic-validated dispatcher. Keep facts
and outcome state process-local until a persistence phase. After `finish_call`,
request a natural final utterance and close only after Twilio acknowledges its
playback. Do not add an approval workflow, UI, recording, summaries, or arbitrary
external-action tools.

### Phase 6 — provider media bridge

Extend the Phase 3 bridge only where later requirements need bounded buffering,
more complete interruption semantics, or codec conversion. Preserve the tested
provider boundaries and direct forwarding while formats remain compatible.

### Phase 7 — controlled end-to-end operation

Add opt-in live integration, operational safeguards, summaries/outcomes,
timeouts, observability, and a documented manual smoke test. Keep actions
non-binding unless a later, separately specified phase adds capability tools.

### Later phases

Potential work includes stronger owner authentication, deployment hardening,
retention controls, localization, richer outcome extraction, and explicitly
authorized action capabilities. An approval workflow remains out of scope
unless this specification and product requirements are deliberately revised.

## 15. Definition of done for a phase

A phase is complete only when:

- its scope is implemented without knowingly pulling in later-phase features;
- prior behavior and tests remain working;
- new domain behavior has automated tests;
- public/configuration behavior is documented;
- secrets and personal data are not introduced into source or fixtures;
- current provider documentation was checked where that phase depends on it;
- architectural changes are reflected here with rationale;
- formatting, linting, typing, and test commands applicable at that point pass;
- changes are committed as a coherent unit.

## 16. Open decisions and ambiguities

These are intentionally unresolved rather than silently assumed:

1. **Owner identity and deployment model:** single trusted local user, private
   hosted service, or internet-facing application; this determines auth and
   CSRF/session requirements.
2. **Jurisdiction and consent:** where owner and destinations are located,
   whether automated-call consent is required, and the exact disclosure text.
3. **Recording/transcripts:** whether either is permitted or desired, and if
   so, consent, retention, encryption, access, and deletion rules.
4. **Authority schema:** which future binding capabilities can be granted and
   how limits, currencies, dates, and revocation are represented. Initial work
   stays non-binding.
5. **Call completion semantics:** voicemail handling, retry policy, maximum
   attempts, calling hours, silence limits, and maximum duration.
6. **Language selection:** whether language is fixed per call, may be detected,
   or may switch during a call; `pt-PT` is the default.
7. **Provider specifics:** the current recommended Realtime model, connection
   authentication, event schema, audio compatibility, and Twilio stream
   details must be verified during their implementation phases.
8. **Summary storage:** required detail, sensitive fields, retention period,
   and whether structured results need human verification.
9. **Scale and recovery:** expected concurrent calls, process topology, and
   behavior after process restarts during an active call.

Future phases should resolve only the decisions necessary for their scope and
record the outcome in this document.
