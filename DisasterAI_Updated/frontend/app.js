// DisasterAI Control Center Frontend Logic
// Version 2.1 — Stage-Wise Results + Live Refresh

// ✅ FIX: Auto-detect backend URL so APK WebView works without hardcoding
const API_BASE = window.location.origin.includes("localhost") || 
                 window.location.origin.includes("127.0.0.1") ||
                 window.location.protocol === "file:"
  ? "http://localhost:8000"
  : window.location.origin;

// API key — fetched from backend config endpoint at startup
let _apiKey = "";
fetch(`${API_BASE}/api/config`).then(r => r.json()).then(d => { if (d.api_key) _apiKey = d.api_key; }).catch(() => {});
const authHeaders = () => _apiKey ? { "X-API-Key": _apiKey } : {};

// Version 2.1 — Stage-Wise Results + Live Refresh

let currentSelectedIncidentId = null;
let activeCallerSessionId = null;
let lastIngestedStage = null; // Track most recently ingested stage for auto-display

document.addEventListener("DOMContentLoaded", () => {
  initDashboard();
  setupEventListeners();
  setInterval(refreshAll, 15000); // Poll every 15s
});

function initDashboard() {
  refreshAll();
}

// -------------------------------------------------------------
// CORE REFRESH — Always fetches fresh data from backend
// -------------------------------------------------------------
async function refreshAll() {
  const btn = document.getElementById("btnRefreshAll");
  const originalText = btn ? btn.textContent : "";

  // Show loading state on button
  if (btn) {
    btn.textContent = "⟳ Refreshing...";
    btn.disabled = true;
    btn.style.opacity = "0.7";
  }

  // Show a "loading" state on all metric values
  const metricIds = [
    "valTotalReports", "valEmergencies", "valActiveIncidents",
    "valResolvedIncidents", "valNeedsReview", "valNoise"
  ];
  metricIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.opacity = "0.4";
  });

  try {
    await Promise.all([
      fetchDashboardMetrics(),
      fetchIncidents(),
      fetchReports(),
    ]);

    // Flash metric cards to indicate fresh data
    metricIds.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.style.opacity = "1";
        el.classList.add("value-updated");
        setTimeout(() => el.classList.remove("value-updated"), 800);
      }
    });
  } finally {
    if (btn) {
      btn.textContent = originalText || "⟳ Refresh";
      btn.disabled = false;
      btn.style.opacity = "";
    }
  }
}

// -------------------------------------------------------------
// METRICS FETCHING
// -------------------------------------------------------------
async function fetchDashboardMetrics() {
  try {
    const res = await fetch(`${API_BASE}/api/dashboard`, { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to load metrics");
    const data = await res.json();

    document.getElementById("valTotalReports").textContent = (data.reports.total || 0).toLocaleString();
    document.getElementById("valEmergencies").textContent = (data.reports.emergency || 0).toLocaleString();
    document.getElementById("valActiveIncidents").textContent = (data.incidents.active || 0).toLocaleString();
    document.getElementById("valResolvedIncidents").textContent = (data.incidents.resolved || 0).toLocaleString();
    document.getElementById("valNeedsReview").textContent = (data.reports.needs_review || 0).toLocaleString();
    document.getElementById("valNoise").textContent = (data.reports.noise || 0).toLocaleString();

    // Source breakdown string
    const srcMap = data.reports.by_source || {};
    const srcParts = Object.entries(srcMap).map(([k, v]) => `${k}: ${v}`);
    document.getElementById("valSourceBreakdown").textContent = srcParts.join(" | ") || "No reports yet";

    // Urgent breakdown
    const urgMap = data.incidents.urgency_breakdown || {};
    const critical = urgMap.CRITICAL || 0;
    const high = urgMap.HIGH || 0;
    document.getElementById("valActiveUrgent").textContent = `${critical} Critical, ${high} High Urgency`;

    document.getElementById("systemStatusText").textContent = "SYSTEM ACTIVE";
    document.getElementById("systemStatusBadge").className = "system-status-badge";
  } catch (err) {
    console.error("Dashboard metrics error:", err);
    document.getElementById("systemStatusText").textContent = "OFFLINE / ERROR";
    document.getElementById("systemStatusBadge").className = "system-status-badge offline";
  }
}

// -------------------------------------------------------------
// INCIDENTS FETCHING & RENDERING
// -------------------------------------------------------------
async function fetchIncidents() {
  const statusFilter = document.getElementById("filterIncidentStatus").value;
  const search = document.getElementById("searchIncidents").value.trim();

  let url = `${API_BASE}/api/incidents?limit=150`;
  if (statusFilter) url += `&status=${encodeURIComponent(statusFilter)}`;
  if (search) url += `&search=${encodeURIComponent(search)}`;

  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to fetch incidents");
    const data = await res.json();
    renderIncidents(data.incidents || []);
  } catch (err) {
    console.error("Incidents error:", err);
    document.getElementById("incidentsList").innerHTML = `<div class="empty-state">Error loading incidents: ${err.message}</div>`;
  }
}

function renderIncidents(incidents) {
  const container = document.getElementById("incidentsList");
  document.getElementById("incidentCountBadge").textContent = incidents.length;

  if (incidents.length === 0) {
    container.innerHTML = '<div class="empty-state">No incidents match current filter.</div>';
    return;
  }

  container.innerHTML = incidents.map(inc => {
    const urgencyClass = inc.urgency === "CRITICAL" ? "badge-danger" : inc.urgency === "HIGH" ? "badge-warning" : "badge-accent";
    const statusClass = inc.status === "RESOLVED" ? "badge-success" : inc.status === "ESCALATING" ? "badge-danger" : "badge";
    const timeFormatted = inc.updated_at ? new Date(inc.updated_at).toLocaleTimeString() : "--";

    return `
      <div class="incident-card" onclick="openIncidentDetails(${inc.id})">
        <div class="incident-top-bar">
          <span class="incident-title">${escapeHtml(inc.title || "Emergency Incident")}</span>
          <div class="incident-badges">
            <span class="badge ${urgencyClass}">${inc.urgency || "LOW"}</span>
            <span class="badge ${statusClass}">${inc.status}</span>
          </div>
        </div>
        <div class="incident-meta-row">
          <span>📍 ${escapeHtml(inc.location_text || "Location Unknown")}</span>
          <span>👥 ${inc.people_affected || 0} Affected</span>
          <span>📡 ${inc.source_count || 1} Sources</span>
          <span>🕒 ${timeFormatted}</span>
        </div>
      </div>
    `;
  }).join("");
}

// -------------------------------------------------------------
// REPORTS STREAM FETCHING & RENDERING
// -------------------------------------------------------------
async function fetchReports() {
  const sourceFilter = document.getElementById("filterReportSource").value;
  const triageFilter = document.getElementById("filterReportEmergency").value;
  const stageFilter = document.getElementById("filterReportStage") ? document.getElementById("filterReportStage").value : "";
  const search = document.getElementById("searchReports").value.trim();

  let url = `${API_BASE}/api/reports?limit=150`;
  if (sourceFilter) url += `&source=${encodeURIComponent(sourceFilter)}`;
  if (triageFilter === "emergency") url += `&is_emergency=true`;
  if (triageFilter === "noise") url += `&is_emergency=false`;
  if (triageFilter === "review") url += `&needs_review=true`;
  if (stageFilter) url += `&stage=${encodeURIComponent(stageFilter)}`;
  if (search) url += `&search=${encodeURIComponent(search)}`;

  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to fetch reports");
    const data = await res.json();
    renderReports(data.reports || []);
  } catch (err) {
    console.error("Reports error:", err);
    document.getElementById("reportsList").innerHTML = `<div class="empty-state">Error loading reports: ${err.message}</div>`;
  }
}

function renderReports(reports) {
  const container = document.getElementById("reportsList");
  document.getElementById("reportCountBadge").textContent = reports.length;

  if (reports.length === 0) {
    container.innerHTML = '<div class="empty-state">No disaster reports found for selected filters.</div>';
    return;
  }

  container.innerHTML = reports.map(r => {
    const isEmergBadge = r.is_emergency 
      ? '<span class="badge badge-danger">EMERGENCY</span>' 
      : '<span class="badge">NOISE / INFO</span>';

    const reviewBadge = r.needs_review 
      ? '<span class="badge badge-warning" title="Flagged for human triage">NEEDS REVIEW</span>' 
      : '';

    const stageBadge = r.stage
      ? `<span class="badge badge-stage" title="Dataset Stage">${r.stage.toUpperCase()}</span>`
      : '';

    const sourceTagColor = r.source === "AUDIO" ? "color: #a855f7" : r.source === "TWITTER" ? "color: #38bdf8" : r.source === "WHATSAPP" ? "color: #22c55e" : "color: #e2e8f0";
    const timeFormatted = r.timestamp ? new Date(r.timestamp).toLocaleTimeString() : "--";

    return `
      <div class="report-card">
        <div class="report-card-header">
          <div style="display:flex; gap:0.5rem; align-items:center;">
            <span class="report-source-tag" style="${sourceTagColor}">[${escapeHtml(r.source)}]</span>
            ${r.source_id ? `<span style="font-family:monospace; font-size:0.7rem; color:#64748b;">${escapeHtml(r.source_id)}</span>` : ""}
          </div>
          <div style="display:flex; gap:0.35rem;">
            ${isEmergBadge}
            ${reviewBadge}
            ${stageBadge}
          </div>
        </div>
        <p class="report-text">${escapeHtml(r.message)}</p>
        <div class="report-card-footer">
          <span>${r.location_text ? `📍 ${escapeHtml(r.location_text)}` : "📍 Unspecified"}</span>
          <div style="display:flex; gap:0.6rem; align-items:center;">
            ${r.incident_id ? `<span style="color:var(--accent-cyan);">Linked #INC-${r.incident_id}</span>` : ""}
            <span>${timeFormatted}</span>
            <button class="btn btn-sm btn-secondary" onclick="toggleReview(${r.id}, ${!r.needs_review})" title="Toggle review flag">
              ${r.needs_review ? "✓ Mark Reviewed" : "Flag Review"}
            </button>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

// -------------------------------------------------------------
// STAGE INGESTION PROCESSING
// -------------------------------------------------------------
async function handleProcessStage() {
  const stage = document.getElementById("selectStage").value;
  const variant = document.getElementById("selectVariant").value;
  const btn = document.getElementById("btnProcessStage");
  const spinner = document.getElementById("stageSpinner");
  const btnText = document.getElementById("btnProcessText");
  const feedback = document.getElementById("ingestionFeedback");

  btn.disabled = true;
  spinner.classList.remove("hidden");
  btnText.textContent = "Processing Multimodal Stream (Audio, Text, Social)...";

  try {
    const res = await fetch(`${API_BASE}/api/dataset/stage/${stage}?variant=${variant}`, {
      method: "POST",
      headers: { ...authHeaders() }
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Stage ingestion failed");
    }

    // Track last ingested stage for the stage results panel
    lastIngestedStage = stage;

    // Populate feedback
    feedback.classList.remove("hidden");
    document.getElementById("feedbackBadge").textContent = `${data.stage.toUpperCase()} INGESTED SUCCESSFULLY`;
    document.getElementById("feedbackPath").textContent = data.dataset.stage_path || "";

    const r = data.results;
    document.getElementById("feedbackStats").innerHTML = `
      <div class="stat-chip">
        <span class="stat-chip-label">New Reports Created</span>
        <span class="stat-chip-val text-success">${r.reports_created}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">Duplicates Skipped</span>
        <span class="stat-chip-val">${r.duplicates_skipped}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">Emergencies Detected</span>
        <span class="stat-chip-val text-danger">${r.emergencies_detected}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">New Incidents Created</span>
        <span class="stat-chip-val text-warning">${r.new_incidents}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">Matched to Existing</span>
        <span class="stat-chip-val text-cyan">${r.matched_existing_incidents}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">Needs Review</span>
        <span class="stat-chip-val">${r.needs_human_review}</span>
      </div>
    `;

    // Show and populate stage results panel for this stage
    await loadStageResults(stage);

    // Refresh entire UI
    await refreshAll();

    // Auto-select the stage in the stage results tab
    const stageResultsSelect = document.getElementById("stageResultsSelect");
    if (stageResultsSelect) {
      stageResultsSelect.value = stage;
    }
  } catch (err) {
    alert("Error during stage ingestion: " + err.message);
  } finally {
    btn.disabled = false;
    spinner.classList.add("hidden");
    btnText.textContent = "Process Stage Multimodal Ingestion";
  }
}

// -------------------------------------------------------------
// STAGE-WISE RESULTS PANEL
// -------------------------------------------------------------
async function loadStageResults(stageId) {
  if (!stageId) return;

  const panel = document.getElementById("stageResultsPanel");
  const stageTitle = document.getElementById("stageResultsTitle");
  const summaryContainer = document.getElementById("stageResultsSummary");
  const reportsContainer = document.getElementById("stageResultsReports");
  const loadingEl = document.getElementById("stageResultsLoading");

  panel.classList.remove("hidden");
  stageTitle.textContent = `Results: ${stageId.toUpperCase()}`;
  loadingEl.classList.remove("hidden");
  summaryContainer.innerHTML = "";
  reportsContainer.innerHTML = "";

  try {
    const res = await fetch(`${API_BASE}/api/dataset/stage/${stageId}/results?limit=200`, { cache: "no-store" });
    if (!res.ok) throw new Error(`Failed to load stage ${stageId} results`);
    const data = await res.json();

    const s = data.summary;
    loadingEl.classList.add("hidden");

    // Summary chips
    summaryContainer.innerHTML = `
      <div class="stage-summary-grid">
        <div class="stage-stat-card">
          <span class="stage-stat-icon">📊</span>
          <span class="stage-stat-num">${s.total_reports}</span>
          <span class="stage-stat-lbl">Total Reports</span>
        </div>
        <div class="stage-stat-card emergency">
          <span class="stage-stat-icon">🚨</span>
          <span class="stage-stat-num">${s.emergency_reports}</span>
          <span class="stage-stat-lbl">Emergencies</span>
        </div>
        <div class="stage-stat-card noise">
          <span class="stage-stat-icon">🔇</span>
          <span class="stage-stat-num">${s.noise_reports}</span>
          <span class="stage-stat-lbl">Noise/Info</span>
        </div>
        <div class="stage-stat-card review">
          <span class="stage-stat-icon">👁️</span>
          <span class="stage-stat-num">${s.needs_review}</span>
          <span class="stage-stat-lbl">Needs Review</span>
        </div>
        <div class="stage-stat-card incident">
          <span class="stage-stat-icon">🔥</span>
          <span class="stage-stat-num">${s.linked_incidents}</span>
          <span class="stage-stat-lbl">Linked Incidents</span>
        </div>
        <div class="stage-stat-card sources">
          <span class="stage-stat-icon">📡</span>
          <span class="stage-stat-num">${Object.entries(s.by_source || {}).map(([k, v]) => `${k}:${v}`).join(' ')}</span>
          <span class="stage-stat-lbl">Source Breakdown</span>
        </div>
      </div>
    `;

    // Reports list for this stage
    const reports = data.reports || [];
    if (reports.length === 0) {
      reportsContainer.innerHTML = '<div class="empty-state">No reports found for this stage. Process the stage first.</div>';
    } else {
      reportsContainer.innerHTML = reports.map(r => {
        const isEmergBadge = r.is_emergency
          ? '<span class="badge badge-danger">EMERGENCY</span>'
          : '<span class="badge">NOISE</span>';
        const reviewBadge = r.needs_review
          ? '<span class="badge badge-warning">REVIEW</span>'
          : '';
        const sourceColor = r.source === "AUDIO" ? "#a855f7" : r.source === "TWITTER" ? "#38bdf8" : r.source === "WHATSAPP" ? "#22c55e" : "#e2e8f0";
        return `
          <div class="report-card compact-report">
            <div class="report-card-header">
              <div style="display:flex;gap:0.5rem;align-items:center;">
                <span style="color:${sourceColor};font-weight:600;font-size:0.75rem;">[${escapeHtml(r.source)}]</span>
                ${r.source_id ? `<span style="font-family:monospace;font-size:0.65rem;color:#64748b;">${escapeHtml(r.source_id)}</span>` : ""}
              </div>
              <div style="display:flex;gap:0.3rem;">${isEmergBadge}${reviewBadge}</div>
            </div>
            <p class="report-text" style="font-size:0.8rem;">${escapeHtml(r.message)}</p>
            <div class="report-card-footer" style="font-size:0.72rem;">
              <span>${r.location_text ? `📍 ${escapeHtml(r.location_text)}` : "📍 Unspecified"}</span>
              <span>${r.incident_id ? `🔗 INC-${r.incident_id}` : ""} ${r.timestamp ? new Date(r.timestamp).toLocaleTimeString() : ""}</span>
            </div>
          </div>
        `;
      }).join("");
    }

  } catch (err) {
    loadingEl.classList.add("hidden");
    summaryContainer.innerHTML = `<div class="empty-state text-danger">Error: ${err.message}</div>`;
  }
}

async function handleViewStageResults() {
  const stage = document.getElementById("stageResultsSelect").value;
  if (!stage) return;
  await loadStageResults(stage);
}

// -------------------------------------------------------------
// INCIDENT DETAILS MODAL
// -------------------------------------------------------------
async function openIncidentDetails(incidentId) {
  currentSelectedIncidentId = incidentId;
  const modal = document.getElementById("incidentModal");

  try {
    const res = await fetch(`${API_BASE}/api/incidents/${incidentId}`, { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to load incident");
    const data = await res.json();
    const inc = data.incident;
    const reports = data.reports || [];

    document.getElementById("modalIncidentTitle").textContent = inc.title;
    document.getElementById("modalIncidentUrgency").textContent = inc.urgency || "LOW";
    document.getElementById("modalIncidentUrgency").className = `badge ${inc.urgency === 'CRITICAL' ? 'badge-danger' : 'badge-warning'}`;
    document.getElementById("modalIncidentId").textContent = `#INC-${inc.id}`;
    document.getElementById("modalIncidentLocation").textContent = inc.location_text || "Unspecified";
    document.getElementById("modalIncidentStatus").textContent = inc.status;
    document.getElementById("modalIncidentPeople").textContent = `${inc.people_affected || 0} People`;
    document.getElementById("modalIncidentSources").textContent = `${inc.source_count || reports.length} Reports`;
    document.getElementById("modalIncidentConfidence").textContent = inc.confidence ? `${Math.round(inc.confidence * 100)}%` : "--";

    document.getElementById("modalLinkedCount").textContent = reports.length;
    document.getElementById("modalLinkedReports").innerHTML = reports.map(r => `
      <div class="report-card" style="padding:0.7rem; background:#1e293b;">
        <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:var(--text-muted);">
          <span>[${escapeHtml(r.source)}] ${r.source_id ? escapeHtml(r.source_id) : ""}</span>
          <span>${r.timestamp ? new Date(r.timestamp).toLocaleTimeString() : ""}</span>
        </div>
        <p style="font-size:0.82rem; margin-top:0.25rem;">${escapeHtml(r.message)}</p>
      </div>
    `).join("");

    modal.classList.remove("hidden");
  } catch (err) {
    alert("Could not load incident details: " + err.message);
  }
}

async function updateIncidentStatus(newStatus) {
  if (!currentSelectedIncidentId) return;

  try {
    const res = await fetch(`${API_BASE}/api/incidents/${currentSelectedIncidentId}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus })
    });
    if (!res.ok) throw new Error("Status update failed");
    openIncidentDetails(currentSelectedIncidentId);
    await refreshAll();
  } catch (err) {
    alert("Error updating status: " + err.message);
  }
}

// -------------------------------------------------------------
// TOGGLE HUMAN REVIEW
// -------------------------------------------------------------
async function toggleReview(reportId, targetFlag) {
  try {
    const res = await fetch(`${API_BASE}/api/reports/${reportId}/review`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ needs_review: targetFlag })
    });
    if (!res.ok) throw new Error("Failed to update review status");
    await refreshAll();
  } catch (err) {
    alert("Failed to update review status: " + err.message);
  }
}

// -------------------------------------------------------------
// MANUAL REPORT SUBMISSION
// -------------------------------------------------------------
async function handleSubmitReport() {
  const source = document.getElementById("inputSource").value;
  const sourceId = document.getElementById("inputSourceId").value.trim() || undefined;
  const message = document.getElementById("inputMessage").value.trim();

  if (!message) {
    alert("Please provide report details.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/reports`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ source, source_id: sourceId, message })
    });

    if (!res.ok) throw new Error("Report submission failed");
    const result = await res.json();
    alert(`Report #${result.report_id} ingested! Action: ${result.incident_action || 'Logged'}`);
    document.getElementById("submitReportModal").classList.add("hidden");
    document.getElementById("inputMessage").value = "";
    document.getElementById("inputSourceId").value = "";
    await refreshAll();
  } catch (err) {
    alert("Error submitting report: " + err.message);
  }
}

// -------------------------------------------------------------
// AI CALLER AGENT SIMULATOR
// -------------------------------------------------------------
async function openCallerModal() {
  const modal = document.getElementById("callerModal");
  modal.classList.remove("hidden");
  const chatWin = document.getElementById("callerChatWindow");
  chatWin.innerHTML = '<div class="empty-state">Initiating AI Intake Line...</div>';

  try {
    const res = await fetch(`${API_BASE}/api/caller/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ caller_phone: "+91 98765 43210" })
    });
    const data = await res.json();
    activeCallerSessionId = data.session_id;

    chatWin.innerHTML = `
      <div class="chat-bubble ai">
        <strong>DisasterAI Intake:</strong><br>${escapeHtml(data.ai_response)}
      </div>
    `;
  } catch (err) {
    chatWin.innerHTML = `<div class="empty-state text-danger">Failed to start caller line: ${err.message}</div>`;
  }
}

async function sendCallerInput(textOverride) {
  const inputEl = document.getElementById("callerInputText");
  const text = textOverride || inputEl.value.trim();
  if (!text || !activeCallerSessionId) return;

  const chatWin = document.getElementById("callerChatWindow");
  chatWin.innerHTML += `
    <div class="chat-bubble caller">
      <strong>Caller:</strong><br>${escapeHtml(text)}
    </div>
  `;
  inputEl.value = "";
  chatWin.scrollTop = chatWin.scrollHeight;

  try {
    const res = await fetch(`${API_BASE}/api/caller/interact`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ session_id: activeCallerSessionId, user_input: text })
    });
    const data = await res.json();

    chatWin.innerHTML += `
      <div class="chat-bubble ai">
        <strong>DisasterAI Intake:</strong><br>${escapeHtml(data.ai_response)}
      </div>
    `;
    chatWin.scrollTop = chatWin.scrollHeight;

    if (data.report_id) {
      await refreshAll();
    }
  } catch (err) {
    chatWin.innerHTML += `
      <div class="chat-bubble ai text-danger">
        Error communicating with caller pipeline: ${err.message}
      </div>
    `;
  }
}

// -------------------------------------------------------------
// EVENT LISTENERS SETUP
// -------------------------------------------------------------
function setupEventListeners() {
  document.getElementById("btnRefreshAll").addEventListener("click", refreshAll);
  document.getElementById("btnProcessStage").addEventListener("click", handleProcessStage);

  // Stage Results View
  document.getElementById("btnViewStageResults").addEventListener("click", handleViewStageResults);

  // Filters
  document.getElementById("filterIncidentStatus").addEventListener("change", fetchIncidents);
  document.getElementById("searchIncidents").addEventListener("input", debounce(fetchIncidents, 300));
  document.getElementById("filterReportSource").addEventListener("change", fetchReports);
  document.getElementById("filterReportEmergency").addEventListener("change", fetchReports);
  document.getElementById("filterReportStage").addEventListener("change", fetchReports);
  document.getElementById("searchReports").addEventListener("input", debounce(fetchReports, 300));

  // Incident Modal
  document.getElementById("btnCloseIncidentModal").addEventListener("click", () => {
    document.getElementById("incidentModal").classList.add("hidden");
  });
  document.querySelectorAll(".btn-status").forEach(btn => {
    btn.addEventListener("click", (e) => {
      const targetStatus = e.target.getAttribute("data-status");
      updateIncidentStatus(targetStatus);
    });
  });

  // Manual Submission Modal
  document.getElementById("btnOpenSubmitModal").addEventListener("click", () => {
    document.getElementById("submitReportModal").classList.remove("hidden");
  });
  document.getElementById("btnCloseSubmitModal").addEventListener("click", () => {
    document.getElementById("submitReportModal").classList.add("hidden");
  });
  document.getElementById("btnCancelSubmit").addEventListener("click", () => {
    document.getElementById("submitReportModal").classList.add("hidden");
  });
  document.getElementById("btnSubmitReport").addEventListener("click", handleSubmitReport);

  // AI Caller Modal
  document.getElementById("btnOpenCallerModal").addEventListener("click", openCallerModal);
  document.getElementById("btnCloseCallerModal").addEventListener("click", () => {
    document.getElementById("callerModal").classList.add("hidden");
  });
  document.getElementById("btnSendCallerInput").addEventListener("click", () => sendCallerInput());
  document.getElementById("callerInputText").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendCallerInput();
  });
  document.getElementById("btnEscalateCaller").addEventListener("click", () => {
    sendCallerInput("operator");
  });

  // Close stage results panel
  document.getElementById("btnCloseStageResults").addEventListener("click", () => {
    document.getElementById("stageResultsPanel").classList.add("hidden");
  });
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}
