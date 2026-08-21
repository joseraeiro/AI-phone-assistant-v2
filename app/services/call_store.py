"""Minimal process-local call registry for correlation and internal tools."""

from threading import RLock
from uuid import UUID

from app.domain.calls import CallConfiguration, CallRuntime


class CallNotFoundError(LookupError):
    """Raised when a provider stream references an unknown call."""


class CallStore:
    """Thread-safe in-memory runtime store; persistence is deliberately deferred."""

    def __init__(self) -> None:
        self._calls: dict[UUID, CallRuntime] = {}
        self._lock = RLock()

    def add(self, configuration: CallConfiguration) -> CallRuntime:
        with self._lock:
            runtime = CallRuntime(configuration)
            self._calls[configuration.internal_call_id] = runtime
            return runtime

    def get(self, internal_call_id: UUID) -> CallRuntime:
        with self._lock:
            try:
                return self._calls[internal_call_id]
            except KeyError as exc:
                raise CallNotFoundError(str(internal_call_id)) from exc

    def remove(self, internal_call_id: UUID) -> None:
        with self._lock:
            self._calls.pop(internal_call_id, None)


call_store = CallStore()


def get_call_store() -> CallStore:
    """Return the process-local call registry."""

    return call_store
