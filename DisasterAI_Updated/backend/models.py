from datetime import datetime, timezone
import json

from sqlalchemy import String, Text, DateTime, Boolean, Integer, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    is_emergency: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    emergency_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    people_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    incident_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    raw_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    incident: Mapped["Incident | None"] = relationship("Incident", back_populates="reports")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    emergency_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    people_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgency: Mapped[str] = mapped_column(String(20), default="LOW")
    status: Mapped[str] = mapped_column(String(30), default="NEW")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reports: Mapped[list["Report"]] = relationship("Report", back_populates="incident")


class CallerSession(Base):
    """Persistent store for AI caller triage sessions — survives server restarts."""
    __tablename__ = "caller_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    caller_phone: Mapped[str] = mapped_column(String(100), default="Anonymous")
    state: Mapped[str] = mapped_column(String(50), default="GREETING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    messages_json: Mapped[str] = mapped_column(Text, default="[]")         # JSON list
    extracted_data_json: Mapped[str] = mapped_column(Text, default="{}")   # JSON dict
    is_escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Helpers for JSON fields
    @property
    def messages(self):
        return json.loads(self.messages_json or "[]")

    @messages.setter
    def messages(self, value):
        self.messages_json = json.dumps(value)

    @property
    def extracted_data(self):
        return json.loads(self.extracted_data_json or "{}")

    @extracted_data.setter
    def extracted_data(self, value):
        self.extracted_data_json = json.dumps(value)
