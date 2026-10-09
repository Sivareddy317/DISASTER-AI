"""
DisasterAI — AI Analysis Service
Rule-based triage engine with optional LLM fallback (Anthropic Claude).
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx

# =========================================================
# KNOWN LOCATION → COORDINATES MAP
# =========================================================
LOCATION_COORDS: Dict[str, tuple] = {
    # Chennai
    "Medavakkam": (12.9229, 80.2004),
    "Velachery MRTS": (12.9816, 80.2209),
    "Tambaram Transformer Area": (12.9236, 80.1007),
    "Tambaram Sanatorium": (12.9236, 80.1007),
    # Andhra Pradesh
    "Kurnool": (15.8281, 78.0373),
    "Proddatur": (14.7500, 78.5500),
    "Vijayawada": (16.5062, 80.6480),
    "Visakhapatnam": (17.6868, 83.2185),
    "Tirupati": (13.6288, 79.4192),
    "Guntur": (16.3067, 80.4365),
    "Nellore": (14.4426, 79.9865),
    # Telangana
    "Hitech City": (17.4435, 78.3772),
    "Secunderabad": (17.4399, 78.4983),
    "Kukatpally": (17.4849, 78.4138),
    "Warangal": (17.9784, 79.5941),
    # Bangalore
    "Koramangala": (12.9352, 77.6245),
    "Whitefield": (12.9698, 77.7500),
    "Indiranagar": (12.9784, 77.6408),
    # Mumbai
    "Bandra": (19.0596, 72.8295),
    "Andheri": (19.1136, 72.8697),
    "Kurla": (19.0726, 72.8799),
    # Delhi
    "Connaught Place": (28.6330, 77.2194),
    "Dwarka": (28.5921, 77.0460),
}


def clean_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", str(text).lower().strip())
    cleaned = cleaned.replace("vela chari", "velachery").replace("m.r.t.s", "mrts")
    return cleaned


def normalize_location(location: Optional[str]) -> Optional[str]:
    if not location:
        return None
    text = str(location).lower().strip()
    text = text.replace("vela chari", "velachery").replace("m.r.t.s", "mrts")

    if any(k in text for k in ["medavakkam", "second lane behind school",
        "water tank road", "school compound backside"]):
        return "Medavakkam"
    if any(k in text for k in ["velachery", "mrts", "vijayanagar"]):
        return "Velachery MRTS"
    if any(k in text for k in ["tambaram", "sanatorium", "eb compound", "transformer"]):
        return "Tambaram Transformer Area"
    if any(k in text for k in ["kurnool"]):
        return "Kurnool"
    if any(k in text for k in ["proddatur"]):
        return "Proddatur"
    if any(k in text for k in ["vijayawada"]):
        return "Vijayawada"
    if any(k in text for k in ["visakhapatnam", "vizag"]):
        return "Visakhapatnam"
    if any(k in text for k in ["tirupati"]):
        return "Tirupati"
    if any(k in text for k in ["hitech city", "hitec city"]):
        return "Hitech City"
    if any(k in text for k in ["secunderabad"]):
        return "Secunderabad"
    if any(k in text for k in ["kukatpally"]):
        return "Kukatpally"
    return location.strip()


def get_coords_for_location(location_text: Optional[str]) -> tuple:
    """Return (lat, lon) for a known location, else (None, None)."""
    if not location_text:
        return (None, None)
    if location_text in LOCATION_COORDS:
        return LOCATION_COORDS[location_text]
    loc_lower = location_text.lower()
    for key, coords in LOCATION_COORDS.items():
        if key.lower() in loc_lower or loc_lower in key.lower():
            return coords
    return (None, None)


def extract_people(text: str) -> Optional[int]:
    patterns = [
        r"(\d+)\s+(?:more\s+)?people",
        r"(\d+)\s+(?:more\s+)?persons?",
        r"all\s+(\d+)\s+people",
        r"(\d+)\s+people\s+(?:were|are)\s+affected",
        r"(\d+)\s+(?:people|persons?|members?)\s+(?:are|were)?\s*(?:trapped|evacuated|rescued)",
        r"(\d+)\s+members?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def detect_location(text: str) -> Optional[str]:
    location_map = [
        ("medavakkam", "Medavakkam"),
        ("velachery mrts", "Velachery MRTS"),
        ("velachery", "Velachery MRTS"),
        ("mrts", "Velachery MRTS"),
        ("tambaram sanatorium", "Tambaram Sanatorium"),
        ("sanatorium", "Tambaram Transformer Area"),
        ("tambaram", "Tambaram Transformer Area"),
        ("eb compound", "Tambaram Transformer Area"),
        ("second lane behind school", "Medavakkam"),
        ("water tank road", "Medavakkam"),
        ("vijayanagar", "Velachery MRTS"),
        ("kurnool", "Kurnool"),
        ("proddatur", "Proddatur"),
        ("vijayawada", "Vijayawada"),
        ("visakhapatnam", "Visakhapatnam"),
        ("vizag", "Visakhapatnam"),
        ("tirupati", "Tirupati"),
        ("hitech city", "Hitech City"),
        ("secunderabad", "Secunderabad"),
        ("kukatpally", "Kukatpally"),
        ("koramangala", "Koramangala"),
        ("whitefield", "Whitefield"),
        ("bandra", "Bandra"),
        ("andheri", "Andheri"),
    ]
    for keyword, location in location_map:
        if keyword in text:
            return location
    patterns = [
        r"near\s+([^,.:;!?]+)",
        r"behind\s+([^,.:;!?]+)",
        r"at\s+([^,.:;!?]+)",
        r"from\s+([^,.:;!?]+)",
        r"in\s+([^,.:;!?]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            loc = match.group(1).strip()
            stop_phrases = [" is now", " are being", " was ", " were ",
                " all ", " elderly ", " fire ", " flood ", " safe", " danger"]
            for sp in stop_phrases:
                if sp in loc:
                    loc = loc.split(sp)[0].strip()
            if len(loc) >= 3 and not any(loc.startswith(w) for w in [
                "the moment", "yesterday", "today", "now", "midnight"]):
                return loc.title()
    return None


def analyze_with_llm(message: str) -> Optional[Dict[str, Any]]:
    """Call Claude Haiku for low-confidence reports. Returns dict or None."""
    api_key = os.getenv("AI_PROVIDER_API_KEY", "").strip()
    if not api_key:
        return None

    prompt = f"""You are an emergency triage AI. Classify this disaster report strictly as JSON.

Message: {message}

Return ONLY valid JSON, no markdown, no explanation:
{{
  "is_emergency": true or false,
  "emergency_type": "FIRE" or "FLOOD_RESCUE" or "MEDICAL_RESCUE" or "RESCUE" or null,
  "location_text": "location string" or null,
  "urgency": "CRITICAL" or "HIGH" or "LOW",
  "people_affected": number or null,
  "confidence": 0.0 to 1.0,
  "state_signal": "NEW" or "ACTIVE" or "ESCALATING" or "RESCUE_IN_PROGRESS" or "RESOLVED",
  "resource_needed": "RESCUE_BOAT" or "MEDICAL_SUPPORT" or "FIRE_SERVICE" or null,
  "vulnerable_people": ["ELDERLY","CHILDREN","DIABETIC"] or null
}}"""

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-5-5",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        result["analysis_method"] = "llm"
        return result
    except Exception as exc:
        print(f"[LLM fallback] failed: {exc}")
        return None


def analyze_report(message: str) -> Dict[str, Any]:
    text = clean_text(message)

    if not text:
        return {
            "is_emergency": False, "emergency_type": None,
            "location_text": None, "latitude": None, "longitude": None,
            "people_affected": None, "urgency": "LOW", "confidence": 0.0,
            "needs_review": True, "state_signal": "UNKNOWN",
            "resource_needed": None, "vulnerable_people": None,
            "analysis_method": "rule_based",
        }

    noise_patterns = [
        "swiggy working", "electricity complaint number", "traffic is horrible",
        "confirm train timing", "food delivery services", "rainfall forecast",
        "all schools are closed", "power gone in my street",
        "is metro service running", "claims 500mm rain", "market is open",
        "trains are delayed", "extreme rain at exactly midnight",
        "fake news", "rumor", "complaint number",
    ]
    is_noise = any(pattern in text for pattern in noise_patterns)

    emergency_signals = [
        "rescue", "boat reached", "evacuated", "evacuating", "being moved to safety",
        "fire", "flames", "fire service", "fire tender", "extinguished",
        "insulin", "diabetic patient", "diabetic", "people were affected",
        "trapped", "stuck", "flooding", "flood", "water entered", "injured",
        "casualty", "danger", "medical help", "medical request", "collapsed",
        "water level", "ndrf",
    ]
    has_emergency_signal = any(sig in text for sig in emergency_signals)

    if is_noise and not ("trapped" in text or "insulin" in text or
                         "boat reached" in text or "fire tender" in text):
        return {
            "is_emergency": False, "emergency_type": None,
            "location_text": None, "latitude": None, "longitude": None,
            "people_affected": None, "urgency": "LOW", "confidence": 0.95,
            "needs_review": False, "state_signal": "NOISE",
            "resource_needed": None, "vulnerable_people": None,
            "analysis_method": "rule_based",
        }

    emergency_type = None
    if any(k in text for k in ["transformer fire", "fire near", "fire service",
                                 "fire tender", "extinguished", "flames", "fire", "burning"]):
        emergency_type = "FIRE"
    elif any(k in text for k in ["insulin", "diabetic patient", "diabetic",
                                   "medical request", "medical help", "medicine"]):
        emergency_type = "MEDICAL_RESCUE"
    elif any(k in text for k in ["boat reached", "rescue boat", "evacuated",
                                   "evacuating", "flood", "flooding", "water entered",
                                   "submerged", "water level", "moved to safety", "ndrf"]):
        emergency_type = "FLOOD_RESCUE"
    elif any(k in text for k in ["trapped", "rescue", "stuck", "injured", "accident"]):
        emergency_type = "RESCUE"

    is_emergency = bool(has_emergency_signal or emergency_type)

    if any(p in text for p in ["final update", "fully controlled", "extinguished",
        "rescue completed", "marked resolved", "no additional", "resolved"]):
        state_signal = "RESOLVED"
    elif any(p in text for p in ["rescue is in progress", "being evacuated",
        "being moved to safety", "boat reached", "team arrived", "in progress"]):
        state_signal = "RESCUE_IN_PROGRESS"
    elif any(p in text for p in ["escalating", "water rising", "fire spreading", "critical condition"]):
        state_signal = "ESCALATING"
    elif any(p in text for p in ["update", "same medavakkam", "same velachery", "same tambaram"]):
        state_signal = "UPDATE"
    else:
        state_signal = "NEW"

    if state_signal == "RESOLVED":
        urgency = "LOW"
    elif any(w in text for w in ["critical", "dying", "trapped", "unconscious",
                                   "immediate danger", "life threatening"]):
        urgency = "CRITICAL"
    elif is_emergency:
        urgency = "HIGH"
    else:
        urgency = "LOW"

    people_affected = extract_people(text)
    location_text = detect_location(text)
    if location_text:
        location_text = normalize_location(location_text)

    resource_needed = None
    if "boat" in text or "evacuated" in text or "ndrf" in text:
        resource_needed = "RESCUE_BOAT"
    elif "insulin" in text or "diabetic" in text or "medical" in text:
        resource_needed = "MEDICAL_SUPPORT"
    elif "fire" in text or "fire tender" in text:
        resource_needed = "FIRE_SERVICE"

    vulnerable_people = []
    if "elderly" in text:
        vulnerable_people.append("ELDERLY")
    if "children" in text or "child" in text:
        vulnerable_people.append("CHILDREN")
    if "diabetic" in text or "insulin" in text:
        vulnerable_people.append("DIABETIC")
    if not vulnerable_people:
        vulnerable_people = None

    if is_emergency and emergency_type and location_text:
        confidence = 0.95
    elif is_emergency and emergency_type:
        confidence = 0.85
    elif is_emergency:
        confidence = 0.75
    elif is_noise:
        confidence = 0.95
    else:
        confidence = 0.60

    needs_review = confidence < 0.70 or (is_emergency and not location_text)

    # ✅ FIXED: Resolve lat/lon from location
    lat, lon = get_coords_for_location(location_text)

    result = {
        "is_emergency": is_emergency,
        "emergency_type": emergency_type,
        "location_text": location_text,
        "latitude": lat,
        "longitude": lon,
        "people_affected": people_affected,
        "urgency": urgency,
        "confidence": confidence,
        "needs_review": needs_review,
        "state_signal": state_signal,
        "resource_needed": resource_needed,
        "vulnerable_people": vulnerable_people,
        "analysis_method": "rule_based",
    }

    # ✅ NEW: LLM fallback for low-confidence reports
    if confidence < 0.75:
        llm_result = analyze_with_llm(message)
        if llm_result:
            for key in ["is_emergency", "emergency_type", "urgency", "confidence",
                        "state_signal", "resource_needed", "vulnerable_people", "people_affected"]:
                if key in llm_result and llm_result[key] is not None:
                    result[key] = llm_result[key]
            if llm_result.get("location_text"):
                result["location_text"] = normalize_location(llm_result["location_text"])
                result["latitude"], result["longitude"] = get_coords_for_location(
                    result["location_text"]
                )
            result["needs_review"] = result.get("confidence", 0.6) < 0.70
            result["analysis_method"] = "llm"

    return result
