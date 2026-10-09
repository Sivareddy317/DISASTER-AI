"""
DisasterAI — AI Caller Session Service
Sessions now persisted to DB (survives server restarts).
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend.models import CallerSession, Report
from services.ai_analysis import analyze_report, normalize_location
from services.incident_service import link_report_to_incident

AI_GREETING = (
    "Hello, this is the DisasterAI emergency intake assistant. I am an automated AI assistant. "
    "Please describe your emergency and location. If you need a human operator, say 'operator'."
)

DISCLAIMER = (
    "You are connected to an AI Emergency Triage Assistant. "
    "This system does not automatically dispatch first responders. "
    "For immediate emergency dispatch, you will be escalated to a human operator."
)


def _load_session(session_id: str, db: Session) -> CallerSession:
    session = db.get(CallerSession, session_id)
    if not session:
        raise ValueError(f"Active caller session not found: {session_id}")
    return session


def start_caller_session(caller_phone: str = "Anonymous", db: Session = None) -> Dict[str, Any]:
    session_id = str(uuid.uuid4())

    messages = [{"sender": "AI", "text": AI_GREETING}]
    extracted = {
        "description": None,
        "location": None,
        "immediate_danger": None,
        "people_affected": None,
    }

    if db is not None:
        session = CallerSession(
            id=session_id,
            caller_phone=caller_phone,
            state="GREETING",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.messages = messages
        session.extracted_data = extracted
        db.add(session)
        db.commit()

    return {
        "session_id": session_id,
        "state": "GREETING",
        "ai_response": AI_GREETING,
        "escalated_to_human": False,
        "disclaimer": DISCLAIMER,
    }


def interact_caller_session(
    session_id: str,
    user_input: str,
    db: Session,
) -> Dict[str, Any]:
    session = _load_session(session_id, db)

    text = user_input.strip()
    msgs = session.messages
    msgs.append({"sender": "CALLER", "text": text})
    lower_text = text.lower()

    # Human escalation
    if any(k in lower_text for k in ["operator", "human", "agent", "person", "real person", "help me now"]):
        session.state = "ESCALATED_TO_HUMAN"
        session.is_escalated = True
        response = "Transferring you immediately to a human control room operator. Please stay on the line."
        msgs.append({"sender": "AI", "text": response})
        session.messages = msgs
        session.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {
            "session_id": session_id,
            "state": session.state,
            "ai_response": response,
            "escalated_to_human": True,
            "report_id": None,
        }

    ext = session.extracted_data
    current_state = session.state

    if current_state in {"GREETING", "AWAITING_DESCRIPTION"}:
        ext["description"] = text
        quick_an = analyze_report(text)
        if quick_an.get("location_text"):
            ext["location"] = quick_an["location_text"]
        if quick_an.get("people_affected"):
            ext["people_affected"] = quick_an["people_affected"]

        if not ext["location"]:
            session.state = "AWAITING_LOCATION"
            response = "Understood. What is your exact location or nearest landmark?"
        else:
            session.state = "AWAITING_CONFIRMATION"
            response = (
                f"Thank you. We recorded: '{text}' near '{ext['location']}'. "
                "Are there people in immediate danger? Please confirm."
            )

    elif current_state == "AWAITING_LOCATION":
        ext["location"] = text
        session.state = "AWAITING_CONFIRMATION"
        response = f"Location recorded as {text}. Are there people in immediate danger? Confirm to finalize."

    elif current_state in {"AWAITING_CONFIRMATION", "CONFIRMING"}:
        full_text = (
            f"{ext.get('description', '')}. "
            f"Location: {ext.get('location', '')}. "
            f"Danger details: {text}"
        )
        session.state = "COMPLETED"

        analysis = analyze_report(full_text)
        if ext.get("location"):
            analysis["location_text"] = normalize_location(ext["location"])

        report = Report(
            source="AI_CALLER",
            source_id=f"call_{session_id[:8]}",
            message=full_text,
            timestamp=datetime.now(timezone.utc),
            is_emergency=analysis.get("is_emergency", True),
            emergency_type=analysis.get("emergency_type"),
            location_text=analysis.get("location_text"),
            latitude=analysis.get("latitude"),
            longitude=analysis.get("longitude"),
            people_affected=analysis.get("people_affected") or ext.get("people_affected"),
            urgency=analysis.get("urgency", "HIGH"),
            confidence=analysis.get("confidence", 0.90),
            needs_review=True,
            raw_metadata=f'{{"session_id": "{session_id}", "caller": "{session.caller_phone}"}}',
        )
        db.add(report)
        db.flush()

        incident, action = link_report_to_incident(report, analysis, db)
        session.linked_report_id = report.id

        response = (
            f"Your report has been logged for human operator review (Report #{report.id}). "
            "Help is being coordinated. Stay in a safe location."
        )
        msgs.append({"sender": "AI", "text": response})
        session.messages = msgs
        session.extracted_data = ext
        session.updated_at = datetime.now(timezone.utc)
        db.commit()

        return {
            "session_id": session_id,
            "state": session.state,
            "ai_response": response,
            "escalated_to_human": False,
            "report_id": report.id,
            "incident_id": incident.id if incident else None,
            "incident_action": action,
        }
    else:
        response = "Your report is already logged. An operator has been notified."

    msgs.append({"sender": "AI", "text": response})
    session.messages = msgs
    session.extracted_data = ext
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "session_id": session_id,
        "state": session.state,
        "ai_response": response,
        "escalated_to_human": False,
    }
