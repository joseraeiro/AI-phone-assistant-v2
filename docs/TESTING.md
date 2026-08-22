# Manual end-to-end testing

Use a destination you control or are authorized to call. Ensure `.env` contains live provider credentials, `APP_BASE_URL` is the active HTTPS tunnel origin, `TWILIO_VALIDATE_SIGNATURES=true`, and `DRY_RUN=false`. Keep the FastAPI, tunnel, Twilio call, and application event logs visible.

## Test call configuration

- **Destination:** a verified mobile number in E.164 format
- **Objective:** ask today's closing time and whether the business is open tomorrow morning
- **Context:** release smoke test
- **Preferences:** concise European Portuguese; confirm ambiguous times
- **Constraints:** gather information only; do not reserve, purchase, or schedule
- **Language:** `pt-PT`

## Outbound call

- [ ] Create and start the call from the dashboard.
- [ ] The telephone rings and Twilio Call SID appears.
- [ ] The page changes through calling/ringing to LIVE.
- [ ] Twilio establishes a Stream SID and logs `MEDIA_STREAM_STARTED`.

## Audio

- [ ] The agent introduces itself and is audible without severe distortion.
- [ ] Say “Olá, estás a ouvir-me?” and confirm the agent understands.
- [ ] Continue for several turns; remote voice reaches OpenAI and agent audio reaches Twilio.

## European Portuguese

- [ ] Pronunciation and vocabulary are appropriate for pt-PT.
- [ ] Utterances remain short and telephone-friendly.

## Interruption

- [ ] While the agent is speaking, say loudly “Não, espera.”
- [ ] The old reply stops rapidly instead of finishing queued audio.
- [ ] The agent listens, acknowledges the correction, and continues.
- [ ] Repeat interruption twice; no stale prior audio appears later.

## Goal following and constraints

- [ ] The agent pursues both objective questions and clarifies an unclear time.
- [ ] It asks relevant follow-ups without turning into a monologue.
- [ ] Offer an unauthorized reservation or appointment.
- [ ] The agent politely declines the commitment and continues gathering information.
- [ ] The agent marks success/partial/failed with an operational reason and ends naturally.

## Transcript, facts, and summary

- [ ] Final entries from both speakers appear in sensible order; partial deltas are absent.
- [ ] Confirmed opening times appear as captured facts with suitable confidence.
- [ ] Events include key call, stream, OpenAI, speech, response, and tool transitions.
- [ ] The structured summary accurately represents the conversation and uncertainty.
- [ ] Reopening the call after restarting FastAPI still shows historical data.

## Recording

Run only when recording is lawful and appropriate for the test.

- [ ] `off`: no recording starts.
- [ ] `ask`: the agent asks first. On refusal, no recording starts and conversation continues.
- [ ] `ask`: on clear acceptance, recording begins only afterward.
- [ ] `always`: recording starts automatically as configured.
- [ ] After the callback reports completion, metadata and WAV playback/retrieval are available.

## Hangup and cleanup

- [ ] Remote hangup produces stream/call completion and no zombie live session.
- [ ] In another call, **End Call** is idempotent and terminates Twilio cleanly.
- [ ] No API keys, tokens, raw audio payloads, or complete transcripts appear in logs.
- [ ] Record internal UUID, Call SID, Stream SID, and Recording SID for any defect.
