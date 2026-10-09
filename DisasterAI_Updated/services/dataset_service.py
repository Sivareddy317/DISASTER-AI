import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from backend.models import Incident, Report
from services.ai_analysis import analyze_report, normalize_location
from services.audio_service import transcribe_audio
from services.incident_service import link_report_to_incident


def resolve_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def parse_stage_identifier(stage_input: str) -> Tuple[str, str]:
    """
    Normalizes inputs like 'stage_1', 'stage1', 'Stage-1', '1' into ('stage1', 'stage_1').
    Raises ValueError for invalid inputs.
    """
    raw = str(stage_input).strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    if raw.startswith("stage"):
        stage_num = raw[5:]
    else:
        stage_num = raw

    if stage_num not in {"1", "2", "3", "4"}:
        raise ValueError(
            f"Invalid stage '{stage_input}'. Supported stages are: stage1, stage2, stage3, stage4."
        )

    return f"stage{stage_num}", f"stage_{stage_num}"


def resolve_stage_directory(
    stage_input: str,
    variant: Optional[str] = None
) -> Tuple[Path, str, str]:
    """
    Finds the actual directory on disk for the requested stage.
    Supports variant='large' or variant='standard' / 'small'.
    Returns (stage_dir_path, normalized_stage_key, variant_used).
    """
    stage_key, stage_folder = parse_stage_identifier(stage_input)
    root = resolve_project_root()
    data_dir = root / "data"

    candidates_large = [
        data_dir / "extracted_stage2" / "large_emergency_dataset" / stage_folder,
        data_dir / "large_emergency_dataset" / stage_folder,
    ]
    candidates_standard = [
        data_dir / "extracted_stage1" / "emergency_dataset" / stage_folder,
        data_dir / "emergency_dataset" / stage_folder,
    ]

    selected_path = None
    selected_variant = None

    if variant:
        v = variant.strip().lower()
        if v in {"large", "stage2", "expanded"}:
            for c in candidates_large:
                if c.is_dir():
                    selected_path = c
                    selected_variant = "large"
                    break
        elif v in {"standard", "small", "stage1", "default"}:
            for c in candidates_standard:
                if c.is_dir():
                    selected_path = c
                    selected_variant = "standard"
                    break
        else:
            raise ValueError(f"Unknown dataset variant '{variant}'. Use 'standard' or 'large'.")

    # If no variant requested or requested variant wasn't found, try standard then large
    if not selected_path:
        # Prefer large if available for comprehensive testing, else standard
        for c in candidates_large:
            if c.is_dir():
                selected_path = c
                selected_variant = "large"
                break

    if not selected_path:
        for c in candidates_standard:
            if c.is_dir():
                selected_path = c
                selected_variant = "standard"
                break

    if not selected_path:
        # Check if fallback directory exists under data/{stage_folder}
        direct_candidate = data_dir / stage_folder
        if direct_candidate.is_dir():
            selected_path = direct_candidate
            selected_variant = "direct"
        else:
            raise FileNotFoundError(
                f"Dataset directory for {stage_key} could not be found under {data_dir}."
            )

    return selected_path, stage_key, selected_variant


def clean_cell_value(val: Any) -> Optional[str]:
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    return s if s else None


def parse_timestamp(val: Any) -> datetime:
    if not val or pd.isna(val):
        return datetime.now(timezone.utc)
    try:
        return pd.to_datetime(val).to_pydatetime()
    except Exception:
        return datetime.now(timezone.utc)


def process_tabular_file(
    file_path: Path,
    stage_name: str,
    db: Session
) -> List[Dict[str, Any]]:
    results = []

    try:
        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(file_path, encoding_errors="replace")
        elif suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(file_path)
        else:
            return results
    except Exception as exc:
        results.append({
            "type": "FILE_ERROR",
            "file": file_path.name,
            "status": "ERROR",
            "error": f"Failed to read tabular file {file_path.name}: {exc}"
        })
        return results

    if df.empty:
        return results

    # Detect schema
    cols = {c.strip().lower(): c for c in df.columns}

    if {"sms_id", "message"}.issubset(cols.keys()):
        source = "SMS"
        id_col = cols["sms_id"]
        msg_col = cols["message"]
        time_col = cols.get("timestamp")
    elif {"tweet_id", "text"}.issubset(cols.keys()):
        source = "TWITTER"
        id_col = cols["tweet_id"]
        msg_col = cols["text"]
        time_col = cols.get("timestamp")
    else:
        # Generic tabular fallback if 'message' or 'text' exists
        if "message" in cols:
            source = "TABULAR"
            id_col = cols.get("id") or cols.get("sms_id")
            msg_col = cols["message"]
            time_col = cols.get("timestamp")
        elif "text" in cols:
            source = "TABULAR"
            id_col = cols.get("id") or cols.get("tweet_id")
            msg_col = cols["text"]
            time_col = cols.get("timestamp")
        else:
            results.append({
                "type": "UNSUPPORTED_SCHEMA",
                "file": file_path.name,
                "status": "SKIPPED",
                "error": f"Columns {list(df.columns)} do not match SMS or Twitter schema."
            })
            return results

    for idx, row in df.iterrows():
        try:
            raw_id = clean_cell_value(row[id_col]) if id_col else f"{file_path.stem}_{idx}"
            message = clean_cell_value(row[msg_col])

            if not message:
                results.append({
                    "type": source,
                    "id": raw_id,
                    "status": "SKIPPED_EMPTY_MESSAGE",
                    "file": file_path.name
                })
                continue

            stable_source_id = str(raw_id) if raw_id else None

            # Idempotency check: see if report with same source and source_id already exists
            if stable_source_id:
                existing_report = (
                    db.query(Report)
                    .filter(Report.source == source, Report.source_id == stable_source_id)
                    .first()
                )
                if existing_report:
                    results.append({
                        "type": source,
                        "id": stable_source_id,
                        "status": "SKIPPED_DUPLICATE",
                        "report_id": existing_report.id,
                        "incident_id": existing_report.incident_id,
                        "message": message
                    })
                    continue

            # Run AI Analysis
            analysis = analyze_report(message)
            ts = parse_timestamp(row[time_col]) if time_col else datetime.now(timezone.utc)

            report = Report(
                source=source,
                source_id=stable_source_id,
                message=message,
                timestamp=ts,
                is_emergency=analysis.get("is_emergency"),
                emergency_type=analysis.get("emergency_type"),
                location_text=analysis.get("location_text"),
                latitude=analysis.get("latitude"),
                longitude=analysis.get("longitude"),
                people_affected=analysis.get("people_affected"),
                urgency=analysis.get("urgency"),
                confidence=analysis.get("confidence"),
                needs_review=analysis.get("needs_review", False),
                stage=stage_name,
                raw_metadata=json.dumps({"file": file_path.name, "stage": stage_name})
            )
            db.add(report)
            db.flush()

            # Incident linking & matching
            incident, incident_action = link_report_to_incident(report, analysis, db)

            results.append({
                "type": source,
                "id": stable_source_id,
                "status": "PROCESSED",
                "report_id": report.id,
                "analysis": analysis,
                "incident_id": incident.id if incident else None,
                "incident_action": incident_action,
                "message": message
            })

        except Exception as row_err:
            results.append({
                "type": source,
                "id": str(idx),
                "status": "ERROR",
                "error": f"Row {idx} error: {row_err}",
                "file": file_path.name
            })

    return results


def process_json_file(
    file_path: Path,
    stage_name: str,
    db: Session
) -> List[Dict[str, Any]]:
    results = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as exc:
        results.append({
            "type": "FILE_ERROR",
            "file": file_path.name,
            "status": "ERROR",
            "error": f"Failed to read JSON file {file_path.name}: {exc}"
        })
        return results

    # Support WhatsApp messages list
    messages = []
    if isinstance(data, dict) and "messages" in data and isinstance(data["messages"], list):
        messages = data["messages"]
    elif isinstance(data, list):
        messages = data

    for idx, item in enumerate(messages):
        try:
            if not isinstance(item, dict):
                continue

            message_id = item.get("message_id") or item.get("id") or f"wa_{file_path.stem}_{idx}"
            text = item.get("text") or item.get("message")
            if not text or not str(text).strip():
                continue

            message = str(text).strip()
            stable_source_id = str(message_id)

            # Idempotency check
            existing = (
                db.query(Report)
                .filter(Report.source == "WHATSAPP", Report.source_id == stable_source_id)
                .first()
            )
            if existing:
                results.append({
                    "type": "WHATSAPP",
                    "id": stable_source_id,
                    "status": "SKIPPED_DUPLICATE",
                    "report_id": existing.id,
                    "incident_id": existing.incident_id,
                    "message": message
                })
                continue

            analysis = analyze_report(message)
            ts = parse_timestamp(item.get("timestamp"))

            report = Report(
                source="WHATSAPP",
                source_id=stable_source_id,
                message=message,
                timestamp=ts,
                is_emergency=analysis.get("is_emergency"),
                emergency_type=analysis.get("emergency_type"),
                location_text=analysis.get("location_text"),
                latitude=analysis.get("latitude"),
                longitude=analysis.get("longitude"),
                people_affected=analysis.get("people_affected"),
                urgency=analysis.get("urgency"),
                confidence=analysis.get("confidence"),
                needs_review=analysis.get("needs_review", False),
                stage=stage_name,
                raw_metadata=json.dumps({"file": file_path.name, "sender": item.get("sender"), "stage": stage_name})
            )
            db.add(report)
            db.flush()

            incident, incident_action = link_report_to_incident(report, analysis, db)

            results.append({
                "type": "WHATSAPP",
                "id": stable_source_id,
                "status": "PROCESSED",
                "report_id": report.id,
                "analysis": analysis,
                "incident_id": incident.id if incident else None,
                "incident_action": incident_action,
                "message": message
            })

        except Exception as item_err:
            results.append({
                "type": "WHATSAPP",
                "id": str(idx),
                "status": "ERROR",
                "error": str(item_err),
                "file": file_path.name
            })

    return results


def process_audio_files(
    audio_files: List[Path],
    stage_name: str,
    db: Session,
    max_audio: Optional[int] = None
) -> List[Dict[str, Any]]:
    results = []

    # Limit audio file processing if max_audio is specified
    if max_audio is not None:
        audio_files = audio_files[:max_audio]

    for audio_path in audio_files:
        stable_source_id = f"{stage_name}:{audio_path.name}"

        # Idempotency check: don't re-transcribe if audio already ingested
        existing = (
            db.query(Report)
            .filter(Report.source == "AUDIO", Report.source_id == stable_source_id)
            .first()
        )
        if existing:
            results.append({
                "type": "AUDIO",
                "file": audio_path.name,
                "id": stable_source_id,
                "status": "SKIPPED_DUPLICATE",
                "report_id": existing.id,
                "incident_id": existing.incident_id,
                "transcript": existing.message
            })
            continue

        try:
            transcript = transcribe_audio(str(audio_path), task="translate")

            if not transcript or not transcript.strip():
                results.append({
                    "type": "AUDIO",
                    "file": audio_path.name,
                    "id": stable_source_id,
                    "status": "NO_SPEECH_DETECTED"
                })
                continue

            analysis = analyze_report(transcript)

            report = Report(
                source="AUDIO",
                source_id=stable_source_id,
                message=transcript,
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
                stage=stage_name,
                raw_metadata=json.dumps({"file": audio_path.name, "stage": stage_name})
            )
            db.add(report)
            db.flush()

            incident, incident_action = link_report_to_incident(report, analysis, db)

            results.append({
                "type": "AUDIO",
                "file": audio_path.name,
                "id": stable_source_id,
                "status": "PROCESSED",
                "report_id": report.id,
                "transcript": transcript,
                "analysis": analysis,
                "incident_id": incident.id if incident else None,
                "incident_action": incident_action
            })

        except Exception as exc:
            results.append({
                "type": "AUDIO",
                "file": audio_path.name,
                "id": stable_source_id,
                "status": "ERROR",
                "error": str(exc)
            })

    return results


def process_stage(
    stage_path_or_input: str,
    stage_name_hint: Optional[str] = None,
    db: Session = None,
    variant: Optional[str] = None,
    include_audio: bool = True,
    max_audio: Optional[int] = None
) -> Dict[str, Any]:
    """
    Main entry point for processing a dataset stage.
    Accepts either an explicit folder path or a stage identifier ('stage1', 'stage_2', etc.).
    Set include_audio=False for instant response (< 500ms) on tabular and social streams.
    """
    if db is None:
        raise ValueError("Database session must be provided to process_stage.")

    input_path = Path(stage_path_or_input)
    if input_path.is_dir():
        stage_dir = input_path
        stage_display_name = stage_name_hint or input_path.name
        variant_used = variant or "custom_path"
    else:
        stage_dir, stage_display_name, variant_used = resolve_stage_directory(
            stage_path_or_input,
            variant=variant
        )

    # 1. Discover all tabular files (.csv, .xlsx, .xls)
    tabular_files = sorted(
        f for ext in ("*.csv", "*.xlsx", "*.xls") for f in stage_dir.rglob(ext)
    )

    # 2. Discover WhatsApp / supporting JSON files (excluding manifest/ground_truth)
    json_files = sorted(
        f for f in stage_dir.rglob("*.json")
        if f.name not in {"manifest.json", "ground_truth.json"}
    )

    # 3. Discover audio files (.mp3, .wav)
    audio_files = sorted(
        f for ext in ("*.mp3", "*.wav") for f in stage_dir.rglob(ext)
    )

    all_discovered_files = [f.name for f in tabular_files + json_files + audio_files]

    all_results: List[Dict[str, Any]] = []

    # Process Tabular
    for tab_file in tabular_files:
        tab_res = process_tabular_file(tab_file, stage_display_name, db)
        all_results.extend(tab_res)

    # Process JSON
    for js_file in json_files:
        js_res = process_json_file(js_file, stage_display_name, db)
        all_results.extend(js_res)

    # Process Audio
    if audio_files and include_audio:
        aud_res = process_audio_files(audio_files, stage_display_name, db, max_audio=max_audio)
        all_results.extend(aud_res)
    elif audio_files and not include_audio:
        for audio_path in audio_files:
            all_results.append({
                "type": "AUDIO",
                "file": audio_path.name,
                "status": "SKIPPED_AUDIO_DISABLED",
                "message": "Audio transcription excluded by request for fast processing"
            })

    # Commit all changes safely
    db.commit()

    # Aggregate counts
    sms_count = sum(1 for r in all_results if r.get("type") == "SMS")
    twitter_count = sum(1 for r in all_results if r.get("type") == "TWITTER")
    whatsapp_count = sum(1 for r in all_results if r.get("type") == "WHATSAPP")
    audio_count = sum(1 for r in all_results if r.get("type") == "AUDIO")

    processed_count = sum(1 for r in all_results if r.get("status") == "PROCESSED")
    duplicates_skipped = sum(1 for r in all_results if r.get("status") == "SKIPPED_DUPLICATE")
    errors_count = sum(1 for r in all_results if r.get("status") == "ERROR")

    emergency_count = 0
    noise_count = 0
    review_count = 0
    new_incidents = 0
    matched_incidents = 0

    for r in all_results:
        if r.get("status") == "PROCESSED":
            an = r.get("analysis") or {}
            if an.get("is_emergency"):
                emergency_count += 1
            else:
                noise_count += 1

            if an.get("needs_review"):
                review_count += 1

            action = r.get("incident_action")
            if action == "CREATED_NEW_INCIDENT":
                new_incidents += 1
            elif action == "MATCHED_EXISTING_INCIDENT":
                matched_incidents += 1

    return {
        "stage": stage_display_name,
        "dataset": {
            "stage_path": str(stage_dir),
            "variant": variant_used,
            "discovered_files": all_discovered_files,
            "tabular_files_count": len(tabular_files),
            "json_files_count": len(json_files),
            "audio_files_count": len(audio_files),
            "sms_rows": sms_count,
            "twitter_rows": twitter_count,
            "whatsapp_records": whatsapp_count,
            "audio_records": audio_count,
            "total_records_evaluated": len(all_results)
        },
        "results": {
            "reports_created": processed_count,
            "duplicates_skipped": duplicates_skipped,
            "emergencies_detected": emergency_count,
            "noise_detected": noise_count,
            "needs_human_review": review_count,
            "new_incidents": new_incidents,
            "matched_existing_incidents": matched_incidents,
            "errors": errors_count
        },
        "reports": all_results
    }