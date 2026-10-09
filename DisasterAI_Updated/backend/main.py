import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.database import Base, SessionLocal, engine
from backend.migrate import run_migration
from backend.models import Incident, Report
from services.ai_analysis import analyze_report, normalize_location
from services.caller_service import interact_caller_session, start_caller_session
from services.dataset_service import parse_stage_identifier, process_stage, resolve_stage_directory
from services.incident_service import link_report_to_incident

# =========================================================
# DATABASE & MIGRATION INITIALIZATION
# =========================================================
run_migration()
Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================================================
# OPTIONAL API KEY AUTHENTICATION
# Set APP_API_KEY in .env to enforce it; leave blank for open dev mode
# =========================================================
APP_API_KEY = os.getenv("APP_API_KEY", "").strip()


async def require_api_key(request: Request):
    """
    If APP_API_KEY is set in environment, all write endpoints require
    the header  X-API-Key: <your-key>.
    GET/health endpoints are always public.
    """
    if not APP_API_KEY:
        return  # Dev mode — no key configured, skip check
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return  # Read-only endpoints are public
    key = request.headers.get("X-API-Key", "")
    if key != APP_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key. Pass header: X-API-Key: <your-key>",
        )


# =========================================================
# FASTAPI APP SETUP
# =========================================================
app = FastAPI(
    title="DisasterAI — Emergency Analysis & Incident Management",
    description=(
        "AI-powered multimodal disaster report analysis, incident deduplication, "
        "triage, and control-room response."
    ),
    version="2.1.0",
)

cors_origins_raw = os.getenv("CORS_ORIGINS", "*")
origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# PYDANTIC SCHEMAS
# =========================================================
class ReportRequest(BaseModel):
    source: str = Field(default="API")
    message: str = Field(..., min_length=1)
    source_id: Optional[str] = Field(default=None)


class StatusUpdateRequest(BaseModel):
    status: str


class ReviewUpdateRequest(BaseModel):
    needs_review: bool


class CallerSessionRequest(BaseModel):
    caller_phone: str = Field(default="Anonymous")


class CallerInteractRequest(BaseModel):
    session_id: str
    user_input: str


# =========================================================
# HEALTH CHECK
# =========================================================
@app.get("/health", tags=["System"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(func.now())
        db_status = "connected"
    except Exception:
        db_status = "error"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "service": "DisasterAI Emergency Response System",
        "version": "2.1.0",
        "database": db_status,
        "auth_enabled": bool(APP_API_KEY),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/config", include_in_schema=False)
def get_frontend_config():
    """Returns the API key to the same-origin frontend."""
    return {"api_key": APP_API_KEY}


# =========================================================
# DASHBOARD METRICS
# =========================================================
@app.get("/api/dashboard", tags=["Dashboard"])
def get_dashboard_metrics(db: Session = Depends(get_db)):
    total_reports = db.query(Report).count()
    emergency_reports = db.query(Report).filter(Report.is_emergency == True).count()
    noise_reports = db.query(Report).filter(Report.is_emergency == False).count()
    review_reports = db.query(Report).filter(Report.needs_review == True).count()

    total_incidents = db.query(Incident).count()
    active_incidents = db.query(Incident).filter(Incident.status != "RESOLVED").count()
    resolved_incidents = db.query(Incident).filter(Incident.status == "RESOLVED").count()
    escalating_incidents = db.query(Incident).filter(Incident.status == "ESCALATING").count()
    rescue_in_progress = db.query(Incident).filter(Incident.status == "RESCUE_IN_PROGRESS").count()

    sources_query = db.query(Report.source, func.count(Report.id)).group_by(Report.source).all()
    urgency_query = (
        db.query(Incident.urgency, func.count(Incident.id))
        .filter(Incident.status != "RESOLVED")
        .group_by(Incident.urgency)
        .all()
    )

    return {
        "reports": {
            "total": total_reports,
            "emergency": emergency_reports,
            "noise": noise_reports,
            "needs_review": review_reports,
            "by_source": {src: count for src, count in sources_query},
        },
        "incidents": {
            "total": total_incidents,
            "active": active_incidents,
            "resolved": resolved_incidents,
            "escalating": escalating_incidents,
            "rescue_in_progress": rescue_in_progress,
            "urgency_breakdown": {urg: count for urg, count in urgency_query},
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# =========================================================
# REPORT MANAGEMENT
# =========================================================
@app.post("/api/reports", tags=["Reports"], dependencies=[Depends(require_api_key)])
def create_report(request: ReportRequest, db: Session = Depends(get_db)):
    msg = request.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Report message cannot be empty.")

    if request.source_id:
        existing = db.query(Report).filter(
            Report.source == request.source,
            Report.source_id == request.source_id,
        ).first()
        if existing:
            return {
                "report_id": existing.id,
                "status": "DUPLICATE_REPORT",
                "source": existing.source,
                "incident_id": existing.incident_id,
                "message": existing.message,
                "is_emergency": existing.is_emergency,
            }

    try:
        analysis = analyze_report(msg)
        analysis["original_message"] = msg

        report = Report(
            source=request.source,
            source_id=request.source_id,
            message=msg,
            timestamp=datetime.now(timezone.utc),
            is_emergency=analysis.get("is_emergency"),
            emergency_type=analysis.get("emergency_type"),
            location_text=analysis.get("location_text"),
            latitude=analysis.get("latitude"),
            longitude=analysis.get("longitude"),
            people_affected=analysis.get("people_affected"),
            urgency=analysis.get("urgency"),
            confidence=analysis.get("confidence"),
            needs_review=analysis.get("needs_review", False),
        )
        db.add(report)
        db.flush()

        incident, action = link_report_to_incident(report, analysis, db)
        db.commit()
        db.refresh(report)

        return {
            "report_id": report.id,
            "source": report.source,
            "message": report.message,
            "analysis": analysis,
            "incident_id": incident.id if incident else None,
            "incident_action": action,
            "timestamp": report.timestamp.isoformat(),
        }
    except Exception as err:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process report: {err}")


@app.get("/api/reports", tags=["Reports"])
def get_reports(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    source: Optional[str] = Query(default=None),
    is_emergency: Optional[bool] = Query(default=None),
    needs_review: Optional[bool] = Query(default=None),
    incident_id: Optional[int] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Report)
    if source:
        query = query.filter(Report.source == source.upper())
    if is_emergency is not None:
        query = query.filter(Report.is_emergency == is_emergency)
    if needs_review is not None:
        query = query.filter(Report.needs_review == needs_review)
    if incident_id is not None:
        query = query.filter(Report.incident_id == incident_id)
    if stage:
        stage_key = stage.strip().lower().replace("_", "").replace("-", "")
        if not stage_key.startswith("stage"):
            stage_key = f"stage{stage_key}"
        query = query.filter(Report.stage == stage_key)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(or_(Report.message.ilike(s), Report.location_text.ilike(s)))

    total_count = query.count()
    reports = query.order_by(Report.timestamp.desc(), Report.id.desc()).offset(offset).limit(limit).all()

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "reports": [
            {
                "id": r.id, "source": r.source, "source_id": r.source_id,
                "message": r.message,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "is_emergency": r.is_emergency, "emergency_type": r.emergency_type,
                "location_text": r.location_text, "latitude": r.latitude,
                "longitude": r.longitude, "people_affected": r.people_affected,
                "urgency": r.urgency, "confidence": r.confidence,
                "needs_review": r.needs_review, "incident_id": r.incident_id,
                "stage": r.stage,
            }
            for r in reports
        ],
    }


@app.get("/api/reports/{report_id}", tags=["Reports"])
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    return {
        "id": report.id, "source": report.source, "source_id": report.source_id,
        "message": report.message,
        "timestamp": report.timestamp.isoformat() if report.timestamp else None,
        "is_emergency": report.is_emergency, "emergency_type": report.emergency_type,
        "location_text": report.location_text, "latitude": report.latitude,
        "longitude": report.longitude, "people_affected": report.people_affected,
        "urgency": report.urgency, "confidence": report.confidence,
        "needs_review": report.needs_review, "incident_id": report.incident_id,
        "raw_metadata": report.raw_metadata,
    }


@app.patch("/api/reports/{report_id}/review", tags=["Reports"], dependencies=[Depends(require_api_key)])
def update_report_review(report_id: int, request: ReviewUpdateRequest, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    report.needs_review = request.needs_review
    db.commit()
    return {"report_id": report.id, "needs_review": report.needs_review, "status": "updated"}


# =========================================================
# INCIDENT MANAGEMENT
# =========================================================
@app.get("/api/incidents", tags=["Incidents"])
def get_incidents(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None),
    urgency: Optional[str] = Query(default=None),
    emergency_type: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Incident)
    if status:
        query = query.filter(Incident.status == status.strip().upper())
    if urgency:
        query = query.filter(Incident.urgency == urgency.strip().upper())
    if emergency_type:
        query = query.filter(Incident.emergency_type == emergency_type.strip().upper())
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(or_(Incident.title.ilike(s), Incident.location_text.ilike(s)))

    total_count = query.count()
    incidents = query.order_by(Incident.updated_at.desc(), Incident.id.desc()).offset(offset).limit(limit).all()

    return {
        "total": total_count, "limit": limit, "offset": offset,
        "incidents": [
            {
                "id": inc.id, "title": inc.title,
                "emergency_type": inc.emergency_type, "location_text": inc.location_text,
                "latitude": inc.latitude, "longitude": inc.longitude,
                "people_affected": inc.people_affected, "urgency": inc.urgency,
                "status": inc.status, "confidence": inc.confidence,
                "source_count": inc.source_count,
                "created_at": inc.created_at.isoformat() if inc.created_at else None,
                "updated_at": inc.updated_at.isoformat() if inc.updated_at else None,
            }
            for inc in incidents
        ],
    }


@app.get("/api/incidents/{incident_id}", tags=["Incidents"])
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")
    reports = (
        db.query(Report).filter(Report.incident_id == incident_id)
        .order_by(Report.timestamp.asc()).all()
    )
    return {
        "incident": {
            "id": incident.id, "title": incident.title,
            "emergency_type": incident.emergency_type,
            "location_text": incident.location_text,
            "latitude": incident.latitude, "longitude": incident.longitude,
            "people_affected": incident.people_affected,
            "urgency": incident.urgency, "status": incident.status,
            "confidence": incident.confidence, "source_count": incident.source_count,
            "created_at": incident.created_at.isoformat() if incident.created_at else None,
            "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
        },
        "reports": [
            {
                "id": r.id, "source": r.source, "source_id": r.source_id,
                "message": r.message,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "is_emergency": r.is_emergency, "emergency_type": r.emergency_type,
                "urgency": r.urgency, "confidence": r.confidence,
                "needs_review": r.needs_review,
            }
            for r in reports
        ],
    }


@app.patch("/api/incidents/{incident_id}/status", tags=["Incidents"], dependencies=[Depends(require_api_key)])
def update_incident_status(incident_id: int, request: StatusUpdateRequest, db: Session = Depends(get_db)):
    allowed = {"NEW", "ACTIVE", "ESCALATING", "RESCUE_IN_PROGRESS", "RESOLVED"}
    stat = request.status.strip().upper()
    if stat not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{request.status}'. Allowed: {', '.join(sorted(allowed))}",
        )
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found.")
    incident.status = stat
    incident.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(incident)
    return {"id": incident.id, "title": incident.title,
            "status": incident.status, "updated_at": incident.updated_at.isoformat()}


# =========================================================
# DATASET STAGE PROCESSING
# =========================================================
@app.get("/api/dataset/stages", tags=["Dataset"])
def list_dataset_stages():
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    found_stages = []
    candidates = [
        ("large", data_dir / "extracted_stage2" / "large_emergency_dataset"),
        ("large", data_dir / "large_emergency_dataset"),
        ("standard", data_dir / "extracted_stage1" / "emergency_dataset"),
    ]
    for variant_name, base_path in candidates:
        if base_path.is_dir():
            for folder in base_path.iterdir():
                if folder.is_dir() and folder.name.startswith("stage_"):
                    found_stages.append({
                        "stage_id": folder.name.replace("_", ""),
                        "folder": folder.name,
                        "variant": variant_name,
                        "path": str(folder),
                        "has_audio": (folder / "audio_calls").is_dir(),
                        "has_social": (folder / "social_and_sms").is_dir(),
                    })
    return {"available_stages": found_stages}


@app.get("/api/dataset/stage/{stage_name}/results", tags=["Dataset"])
def get_stage_results(stage_name: str, limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)):
    try:
        stage_key, _ = parse_stage_identifier(stage_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    reports = (
        db.query(Report).filter(Report.stage == stage_key)
        .order_by(Report.timestamp.desc()).limit(limit).all()
    )
    total_reports = db.query(Report).filter(Report.stage == stage_key).count()
    emergency_reports = db.query(Report).filter(Report.stage == stage_key, Report.is_emergency == True).count()
    noise_reports = db.query(Report).filter(Report.stage == stage_key, Report.is_emergency == False).count()
    review_reports = db.query(Report).filter(Report.stage == stage_key, Report.needs_review == True).count()

    stage_incident_ids = (
        db.query(Report.incident_id)
        .filter(Report.stage == stage_key, Report.incident_id.isnot(None))
        .distinct().all()
    )
    incident_ids = [row[0] for row in stage_incident_ids]

    from sqlalchemy import func as sqlfunc
    sources_query = (
        db.query(Report.source, sqlfunc.count(Report.id))
        .filter(Report.stage == stage_key).group_by(Report.source).all()
    )

    return {
        "stage": stage_key,
        "summary": {
            "total_reports": total_reports,
            "emergency_reports": emergency_reports,
            "noise_reports": noise_reports,
            "needs_review": review_reports,
            "by_source": {src: count for src, count in sources_query},
            "linked_incidents": len(incident_ids),
            "incident_ids": incident_ids,
        },
        "reports": [
            {
                "id": r.id, "source": r.source, "source_id": r.source_id,
                "message": r.message,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "is_emergency": r.is_emergency, "emergency_type": r.emergency_type,
                "location_text": r.location_text, "latitude": r.latitude,
                "longitude": r.longitude, "people_affected": r.people_affected,
                "urgency": r.urgency, "confidence": r.confidence,
                "needs_review": r.needs_review, "incident_id": r.incident_id,
                "stage": r.stage,
            }
            for r in reports
        ],
    }


@app.post("/api/dataset/stage/{stage_name}", tags=["Dataset"], dependencies=[Depends(require_api_key)])
def process_dataset_stage(
    stage_name: str,
    variant: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        stage_dir, stage_key, variant_used = resolve_stage_directory(stage_name, variant=variant)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return process_stage(
            stage_path_or_input=str(stage_dir),
            stage_name_hint=stage_key,
            db=db,
            variant=variant_used,
        )
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Stage processing failed: {error}")


# =========================================================
# AI CALLER AGENT
# =========================================================
@app.post("/api/caller/session", tags=["AI Caller Agent"])
def caller_start_session(request: CallerSessionRequest, db: Session = Depends(get_db)):
    return start_caller_session(caller_phone=request.caller_phone, db=db)


@app.post("/api/caller/interact", tags=["AI Caller Agent"])
def caller_interact(request: CallerInteractRequest, db: Session = Depends(get_db)):
    try:
        return interact_caller_session(
            session_id=request.session_id,
            user_input=request.user_input,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Caller interaction failed: {exc}")


# =========================================================
# STATIC FRONTEND
# =========================================================
frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def serve_frontend_root():
        index_file = frontend_dir / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return {"system": "DisasterAI", "status": "online", "version": "2.1.0"}
