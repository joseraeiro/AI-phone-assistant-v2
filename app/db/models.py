"""Durable SQLAlchemy models for calls and meaningful history."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    """Return one timezone-aware UTC timestamp."""

    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC and restore timezone information omitted by SQLite."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Persisted timestamps must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    destination_name: Mapped[str] = mapped_column(String(200))
    destination_number: Mapped[str] = mapped_column(String(16))
    objective: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")
    preferences: Mapped[str] = mapped_column(Text, default="")
    constraints: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(35), default="pt-PT")
    authorized_actions: Mapped[str] = mapped_column(Text, default="[]")

    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    objective_status: Mapped[str] = mapped_column(String(16), default="unknown")
    objective_status_reason: Mapped[str] = mapped_column(Text, default="Not assessed")

    twilio_call_sid: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True
    )
    twilio_stream_sid: Mapped[str | None] = mapped_column(String(64), index=True)
    openai_model: Mapped[str] = mapped_column(String(100))
    openai_voice: Mapped[str] = mapped_column(String(100), default="marin")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_message: Mapped[str | None] = mapped_column(Text)
    summary_json: Mapped[str | None] = mapped_column(Text)
    summary_text: Mapped[str | None] = mapped_column(Text)
    summary_generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    summary_error: Mapped[str | None] = mapped_column(Text)
    summary_status: Mapped[str] = mapped_column(String(16), default="pending")

    transcripts: Mapped[list["TranscriptEntry"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    events: Mapped[list["CallEvent"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    facts: Mapped[list["CapturedFact"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )


class TranscriptEntry(Base):
    __tablename__ = "transcript_entries"
    __table_args__ = (
        UniqueConstraint("call_id", "sequence", name="uq_transcript_call_sequence"),
        Index("ix_transcript_call_sequence", "call_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    speaker: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    call: Mapped[Call] = relationship(back_populates="transcripts")


class CallEvent(Base):
    __tablename__ = "call_events"
    __table_args__ = (
        UniqueConstraint("call_id", "dedupe_key", name="uq_event_call_dedupe"),
        Index("ix_event_call_created", "call_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    call: Mapped[Call] = relationship(back_populates="events")


class CapturedFact(Base):
    __tablename__ = "captured_facts"
    __table_args__ = (Index("ix_fact_call_created", "call_id", "created_at"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(80))
    fact: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    call: Mapped[Call] = relationship(back_populates="facts")
