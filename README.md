# Personal AI Telephone Agent

This repository will contain a Python personal AI telephone agent using Twilio
Voice and the OpenAI Realtime API.

Development is deliberately incremental. The target architecture, safety
boundaries, phase plan, and unresolved decisions live in
[`PROJECT_SPEC.md`](PROJECT_SPEC.md). Every implementation phase must read that
specification, inspect the existing code, run the existing tests, implement one
phase only, and rerun the tests.

The repository is currently at **Phase 0: specification only**. No telephony,
Realtime API, application server, or user interface has been implemented yet.

Do not add provider credentials to this repository. Future configuration will
use environment variables and server-side secrets.
