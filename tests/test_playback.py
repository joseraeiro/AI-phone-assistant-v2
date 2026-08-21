from app.services.playback import AssistantPlaybackTracker


def test_marks_measure_only_audio_confirmed_as_played() -> None:
    tracker = AssistantPlaybackTracker()
    first = tracker.add_audio(
        response_id="response-1",
        item_id="item-1",
        content_index=0,
        duration_ms=20,
    )
    second = tracker.add_audio(
        response_id="response-1",
        item_id="item-1",
        content_index=0,
        duration_ms=20,
    )

    assert first == "assistant-audio-1"
    assert second == "assistant-audio-2"
    assert tracker.acknowledge_mark(first) is True

    interruption = tracker.interrupt()

    assert interruption is not None
    assert interruption.item_id == "item-1"
    assert interruption.audio_end_ms == 20
    assert tracker.is_playing is False
    assert tracker.acknowledge_mark(second) is False


def test_repeated_interruptions_use_new_marks_and_reset_playback() -> None:
    tracker = AssistantPlaybackTracker()
    first = tracker.add_audio(
        response_id="response-1",
        item_id="item-1",
        content_index=0,
        duration_ms=20,
    )
    assert first is not None
    assert tracker.interrupt() is not None
    assert (
        tracker.add_audio(
            response_id="response-1",
            item_id="item-1",
            content_index=0,
            duration_ms=20,
        )
        is None
    )

    second = tracker.add_audio(
        response_id="response-2",
        item_id="item-2",
        content_index=0,
        duration_ms=40,
    )
    assert second == "assistant-audio-2"
    next_interruption = tracker.interrupt()

    assert next_interruption is not None
    assert next_interruption.item_id == "item-2"
    assert next_interruption.audio_end_ms == 0
    assert tracker.is_playing is False


def test_output_completion_waits_for_twilio_playback_mark() -> None:
    tracker = AssistantPlaybackTracker()
    mark = tracker.add_audio(
        response_id="response-1",
        item_id="item-1",
        content_index=0,
        duration_ms=20,
    )
    assert mark is not None

    tracker.output_done("response-1")

    assert tracker.is_playing is True
    assert tracker.interrupt() is not None


def test_completed_and_played_audio_is_not_interrupted() -> None:
    tracker = AssistantPlaybackTracker()
    mark = tracker.add_audio(
        response_id="response-1",
        item_id="item-1",
        content_index=0,
        duration_ms=20,
    )
    assert mark is not None
    tracker.output_done("response-1")
    tracker.acknowledge_mark(mark)

    assert tracker.interrupt() is None
