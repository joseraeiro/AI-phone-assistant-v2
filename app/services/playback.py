"""Track Twilio-buffered assistant audio for accurate interruption truncation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaybackMark:
    """Cumulative playback boundary associated with one Twilio mark."""

    response_id: str
    item_id: str
    content_index: int
    audio_end_ms: int


@dataclass(frozen=True)
class InterruptionPoint:
    """The portion of an assistant item confirmed as played by Twilio."""

    item_id: str
    content_index: int
    audio_end_ms: int


class AssistantPlaybackTracker:
    """Reconcile generated audio, acknowledged playback, clears, and late events."""

    def __init__(self) -> None:
        self.response_id: str | None = None
        self.item_id: str | None = None
        self.content_index = 0
        self.generated_audio_ms = 0.0
        self.played_audio_ms = 0
        self.pending_marks: dict[str, PlaybackMark] = {}
        self.suppressed_response_ids: set[str] = set()
        self._mark_sequence = 0
        self._output_done = False

    @property
    def is_playing(self) -> bool:
        """Whether Twilio still has assistant audio not confirmed as played."""

        return bool(self.pending_marks)

    def add_audio(
        self,
        *,
        response_id: str,
        item_id: str,
        content_index: int,
        duration_ms: float,
    ) -> str | None:
        """Register one outgoing chunk and return a unique Twilio mark name."""

        if response_id in self.suppressed_response_ids:
            return None
        if (
            self.response_id != response_id
            or self.item_id != item_id
            or self.content_index != content_index
        ):
            self._begin_response(response_id, item_id, content_index)

        self.generated_audio_ms += max(duration_ms, 0.0)
        self._mark_sequence += 1
        name = f"assistant-audio-{self._mark_sequence}"
        self.pending_marks[name] = PlaybackMark(
            response_id=response_id,
            item_id=item_id,
            content_index=content_index,
            audio_end_ms=int(self.generated_audio_ms),
        )
        return name

    def acknowledge_mark(self, name: str) -> bool:
        """Record a Twilio playback acknowledgement; ignore cleared stale marks."""

        mark = self.pending_marks.pop(name, None)
        if mark is None:
            return False
        if mark.response_id == self.response_id:
            self.played_audio_ms = max(self.played_audio_ms, mark.audio_end_ms)
        if self._output_done and not self.pending_marks:
            self._reset_active()
        return True

    def output_done(self, response_id: str) -> None:
        """Note generation completion without pretending buffered audio was played."""

        if response_id != self.response_id:
            return
        self._output_done = True
        if not self.pending_marks:
            self._reset_active()

    def interrupt(self) -> InterruptionPoint | None:
        """Suppress stale deltas, clear local playback, and return truncation point."""

        if self.response_id is None or self.item_id is None:
            return None
        point = InterruptionPoint(
            item_id=self.item_id,
            content_index=self.content_index,
            audio_end_ms=self.played_audio_ms,
        )
        self.suppressed_response_ids.add(self.response_id)
        self._reset_active()
        return point

    def cancel_response(self, response_id: str) -> None:
        """Suppress late audio emitted after server-side response cancellation."""

        self.suppressed_response_ids.add(response_id)

    def _begin_response(
        self, response_id: str, item_id: str, content_index: int
    ) -> None:
        self.response_id = response_id
        self.item_id = item_id
        self.content_index = content_index
        self.generated_audio_ms = 0.0
        self.played_audio_ms = 0
        self.pending_marks.clear()
        self._output_done = False

    def _reset_active(self) -> None:
        self.response_id = None
        self.item_id = None
        self.content_index = 0
        self.generated_audio_ms = 0.0
        self.played_audio_ms = 0
        self.pending_marks.clear()
        self._output_done = False
