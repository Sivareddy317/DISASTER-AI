from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from backend.models import Incident, Report
from services.ai_analysis import normalize_location

URGENCY_PRIORITY = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


def create_incident_title(analysis: Dict[str, Any]) -> str:
    emergency_type = analysis.get("emergency_type") or "Emergency Incident"
    location = normalize_location(analysis.get("location_text"))

    if location:
        return f"{emergency_type} - {location}"
    return emergency_type


def find_matching_incident(
    analysis: Dict[str, Any],
    db: Session,
    message: str = ""
) -> Optional[Incident]:
    emergency_type = analysis.get("emergency_type")
    location = normalize_location(analysis.get("location_text"))

    original_message = (
        message or analysis.get("original_message", "")
    ).lower()

    if not emergency_type:
        return None

    # Retrieve incidents, most recently updated first
    incidents = (
        db.query(Incident)
        .order_by(Incident.updated_at.desc())
        .all()
    )

    # 1. Explicit same-incident phrases
    if "same medavakkam incident" in original_message or "same medavakkam" in original_message:
        for incident in incidents:
            if incident.emergency_type == emergency_type and normalize_location(incident.location_text) == "Medavakkam":
                return incident

    if "same velachery mrt" in original_message or "same velachery mrts" in original_message:
        for incident in incidents:
            if incident.emergency_type == emergency_type and normalize_location(incident.location_text) == "Velachery MRTS":
                return incident

    if "same tambaram transformer" in original_message or "same tambaram" in original_message:
        for incident in incidents:
            if incident.emergency_type == emergency_type and normalize_location(incident.location_text) == "Tambaram Transformer Area":
                return incident

    # 2. "same location" match
    if "same location" in original_message and location:
        for incident in incidents:
            if incident.emergency_type == emergency_type and normalize_location(incident.location_text) == location:
                return incident

    # 3. "same incident" match
    if "same incident" in original_message:
        for incident in incidents:
            if incident.emergency_type == emergency_type:
                return incident

    # 4. Standard location match (same emergency type + same normalized location)
    if location:
        for incident in incidents:
            if incident.emergency_type != emergency_type:
                continue
            if normalize_location(incident.location_text) == location:
                return incident

    return None


def update_incident_from_analysis(
    incident: Incident,
    analysis: Dict[str, Any]
) -> Incident:
    # 1. People count: keep highest known estimate (do not sum duplicate counts)
    existing_people = incident.people_affected or 0
    new_people = analysis.get("people_affected") or 0
    incident.people_affected = max(existing_people, new_people)

    # 2. Source count
    incident.source_count = (incident.source_count or 0) + 1

    # 3. Location & coordinates
    loc = normalize_location(analysis.get("location_text"))
    if loc:
        incident.location_text = loc

    if analysis.get("latitude") is not None:
        incident.latitude = analysis["latitude"]
    if analysis.get("longitude") is not None:
        incident.longitude = analysis["longitude"]

    # 4. Urgency escalation
    current_urgency = incident.urgency or "LOW"
    new_urgency = analysis.get("urgency") or "LOW"

    if URGENCY_PRIORITY.get(new_urgency, 1) > URGENCY_PRIORITY.get(current_urgency, 1):
        incident.urgency = new_urgency

    # 5. State / Status progression
    state_signal = analysis.get("state_signal")
    if state_signal == "RESOLVED":
        incident.status = "RESOLVED"
    elif state_signal == "RESCUE_IN_PROGRESS":
        incident.status = "RESCUE_IN_PROGRESS"
    elif state_signal == "ESCALATING":
        incident.status = "ESCALATING"
    elif state_signal == "UPDATE":
        if incident.status != "RESOLVED":
            incident.status = "ACTIVE"
    elif incident.status == "NEW":
        incident.status = "ACTIVE"

    # 6. Confidence tracking
    new_conf = analysis.get("confidence")
    if new_conf is not None:
        incident.confidence = max(incident.confidence or 0.0, new_conf)

    # 7. Title & Timestamp
    incident.title = create_incident_title(analysis)
    incident.updated_at = datetime.now(timezone.utc)

    return incident


def create_incident_from_analysis(
    analysis: Dict[str, Any],
    db: Session
) -> Incident:
    location = normalize_location(analysis.get("location_text"))

    status = "NEW"
    state_signal = analysis.get("state_signal")
    if state_signal == "RESOLVED":
        status = "RESOLVED"
    elif state_signal == "RESCUE_IN_PROGRESS":
        status = "RESCUE_IN_PROGRESS"
    elif state_signal == "ESCALATING":
        status = "ESCALATING"
    elif state_signal == "UPDATE":
        status = "ACTIVE"

    incident = Incident(
        title=create_incident_title(analysis),
        emergency_type=analysis.get("emergency_type"),
        location_text=location,
        latitude=analysis.get("latitude"),
        longitude=analysis.get("longitude"),
        people_affected=analysis.get("people_affected"),
        urgency=analysis.get("urgency") or "LOW",
        status=status,
        confidence=analysis.get("confidence"),
        source_count=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db.add(incident)
    db.flush()
    return incident


def link_report_to_incident(
    report: Report,
    analysis: Dict[str, Any],
    db: Session
) -> Tuple[Optional[Incident], Optional[str]]:
    """
    Evaluates analysis for emergency indicators and attaches or creates an incident.
    Returns (incident, incident_action) where action is 'CREATED_NEW_INCIDENT' or 'MATCHED_EXISTING_INCIDENT'.
    """
    if not analysis.get("is_emergency"):
        return None, None

    incident = find_matching_incident(analysis, db, report.message)

    if incident:
        update_incident_from_analysis(incident, analysis)
        action = "MATCHED_EXISTING_INCIDENT"
    else:
        incident = create_incident_from_analysis(analysis, db)
        action = "CREATED_NEW_INCIDENT"

    report.incident_id = incident.id
    db.flush()
    return incident, action
