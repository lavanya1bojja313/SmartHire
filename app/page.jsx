"use client";
import { useState, useEffect, useCallback } from "react";

// ─── Synthetic data ────────────────────────────────────────────────────────
const MOCK_REQUESTS = [
  { id: "r1", candidate_name: "Priya Nair", candidate_email: "priya@gmail.com", position_title: "Senior Engineer", state: "negotiating", loop_count: 2, scheduled_at: null, updated_at: "2025-03-14T10:22:00Z" },
  { id: "r2", candidate_name: "Marcus Webb", candidate_email: "mwebb@outlook.com", position_title: "Product Manager", state: "scheduled", loop_count: 3, scheduled_at: "2025-03-18T14:00:00Z", updated_at: "2025-03-13T16:45:00Z" },
  { id: "r3", candidate_name: "Selin Çelik", candidate_email: "selin@hey.com", position_title: "Design Lead", state: "outreach_sent", loop_count: 0, scheduled_at: null, updated_at: "2025-03-14T09:01:00Z" },
  { id: "r4", candidate_name: "Jin-ho Park", candidate_email: "jinho@kakao.com", position_title: "Staff Engineer", state: "human_intervention", loop_count: 4, scheduled_at: null, updated_at: "2025-03-13T11:30:00Z" },
  { id: "r5", candidate_name: "Amara Osei", candidate_email: "amara@proton.me", position_title: "Data Scientist", state: "draft", loop_count: 0, scheduled_at: null, updated_at: "2025-03-14T08:15:00Z" },
  { id: "r6", candidate_name: "Felipe Duarte", candidate_email: "fduarte@corp.io", position_title: "Backend Engineer", state: "scheduled", loop_count: 1, scheduled_at: "2025-03-19T10:30:00Z", updated_at: "2025-03-12T20:10:00Z" },
  { id: "r7", candidate_name: "Yuki Tanaka", candidate_email: "yuki@fastmail.com", position_title: "ML Engineer", state: "failed", loop_count: 3, scheduled_at: null, updated_at: "2025-03-11T14:00:00Z" },
  { id: "r8", candidate_name: "Chloe Beaumont", candidate_email: "chloe@gmail.com", position_title: "Frontend Engineer", state: "negotiating", loop_count: 1, scheduled_at: null, updated_at: "2025-03-14T11:55:00Z" },
];

const MOCK_AUDIT = [
  { id: "a1", actor: "system", event_type: "request_created", from_state: null, to_state: "draft", created_at: "2025-03-11T09:00:00Z", metadata: {} },
  { id: "a2", actor: "recruiter", event_type: "request_updated", from_state: null, to_state: null, created_at: "2025-03-11T09:05:00Z", metadata: { changes: { position_title: { from: "Engineer", to: "Staff Engineer" } } } },
  { id: "a3", actor: "agent", event_type: "state_transition", from_state: "draft", to_state: "outreach_sent", created_at: "2025-03-11T09:10:00Z", metadata: { email_id: "ses_abc123" } },
  { id: "a4", actor: "agent", event_type: "state_transition", from_state: "outreach_sent", to_state: "negotiating", created_at: "2025-03-12T14:22:00Z", metadata: { candidate_reply: "I'm free Thursday afternoon" } },
  { id: "a5", actor: "agent", event_type: "email_sent", from_state: null, to_state: null, created_at: "2025-03-12T14:23:00Z", metadata: { proposed_slots: ["Thu 2pm", "Thu 4pm"] } },
];

// ─── Helpers ───────────────────────────────────────────────────────────────
const STATE_CONFIG = {
  draft: { label: "Draft", color: "#6B7280", bg: "rgba(107,114,128,0.12)", dot: "#9CA3AF" },
  outreach_sent: { label: "Outreach Sent", color: "#3B82F6", bg: "rgba(59,130,246,0.12)", dot: "#60A5FA" },
  negotiating: { label: "Negotiating", color: "#F59E0B", bg: "rgba(245,158,11,0.12)", dot: "#FCD34D" },
  scheduled: { label: "Scheduled", color: "#10B981", bg: "rgba(16,185,129,0.12)", dot: "#34D399" },
  failed: { label: "Failed", color: "#EF4444", bg: "rgba(239,68,68,0.12)", dot: "#F87171" },
  human_intervention: { label: "Needs Review", color: "#8B5CF6", bg: "rgba(139,92,246,0.12)", dot: "#A78BFA" },
  cancelled: { label: "Cancelled", color: "#6B7280", bg: "rgba(107,114,128,0.08)", dot: "#6B7280" },
};

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  // Use UTC methods to avoid server/client timezone mismatch (hydration error)
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = d.getUTCDate();
  const month = months[d.getUTCMonth()];
  const hour = String(d.getUTCHours()).padStart(2, "0");
  const min = String(d.getUTCMinutes()).padStart(2, "0");
  return `${day} ${month}, ${hour}:${min}`;
};

const fmtRelative = (iso) => {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
};

const initials = (name) => name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2);

const AVATAR_PALETTE = ["#6366F1", "#EC4899", "#F59E0B", "#10B981", "#3B82F6", "#8B5CF6", "#EF4444", "#14B8A6"];
const avatarColor = (name) => AVATAR_PALETTE[name.charCodeAt(0) % AVATAR_PALETTE.length];

// ─── Sub-components ────────────────────────────────────────────────────────
function StateBadge({ state }) {
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.draft;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "3px 9px", borderRadius: 20,
      background: cfg.bg, color: cfg.color,
      fontSize: 11, fontWeight: 600, letterSpacing: "0.04em",
      border: `1px solid ${cfg.color}30`,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: cfg.dot, flexShrink: 0 }} />
      {cfg.label}
    </span>
  );
}

function Avatar({ name, size = 34 }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: "50%",
      background: avatarColor(name),
      display: "flex", alignItems: "center", justifyContent: "center",
      fontSize: size * 0.36, fontWeight: 700, color: "#fff",
      flexShrink: 0, fontFamily: "'DM Mono', monospace",
    }}>
      {initials(name)}
    </div>
  );
}

function MetricCard({ label, value, sub, accent }) {
  return (
    <div style={{
      background: "rgba(255,255,255,0.035)", border: "1px solid rgba(255,255,255,0.08)",
      borderRadius: 14, padding: "20px 22px",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", color: "#6B7280", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 32, fontWeight: 800, color: accent || "#F9FAFB", lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: "#6B7280" }}>{sub}</div>}
    </div>
  );
}

function AuditTimeline({ entries }) {
  const iconFor = (event_type) => {
    if (event_type.includes("created")) return "✦";
    if (event_type.includes("transition")) return "→";
    if (event_type.includes("email")) return "✉";
    if (event_type.includes("override")) return "⚡";
    if (event_type.includes("escalat")) return "⚑";
    return "·";
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {entries.map((e, i) => (
        <div key={e.id} style={{ display: "flex", gap: 14, position: "relative" }}>
          {/* Vertical line */}
          {i < entries.length - 1 && (
            <div style={{ position: "absolute", left: 15, top: 28, width: 1, bottom: -4, background: "rgba(255,255,255,0.07)" }} />
          )}
          <div style={{
            width: 30, height: 30, borderRadius: "50%", flexShrink: 0,
            background: "rgba(99,102,241,0.15)", border: "1px solid rgba(99,102,241,0.3)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 11, color: "#818CF8", marginTop: 4,
          }}>{iconFor(e.event_type)}</div>
          <div style={{ paddingBottom: 20, flex: 1 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: "#E5E7EB", textTransform: "capitalize" }}>
                {e.event_type.replace(/_/g, " ")}
              </span>
              <span style={{ fontSize: 11, color: "#6B7280", whiteSpace: "nowrap", marginLeft: 8 }}>
                {fmtDate(e.created_at)}
              </span>
            </div>
            <div style={{ fontSize: 12, color: "#9CA3AF", marginTop: 2 }}>
              by <span style={{ color: "#D1D5DB", fontWeight: 500 }}>{e.actor}</span>
              {e.from_state && <> &nbsp;·&nbsp; {e.from_state} → {e.to_state}</>}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Side panel ────────────────────────────────────────────────────────────
function DetailPanel({ request, onClose, onAction, token }) {
  const [tab, setTab] = useState("overview");
  const [auditLog, setAuditLog] = useState([]);
  const [loadingAudit, setLoadingAudit] = useState(false);

  useEffect(() => {
    if (tab === "audit" && request && token) {
      setLoadingAudit(true);
      fetch(`http://localhost:8000/api/v1/requests/${request.id}/audit`, {
        headers: { "Authorization": `Bearer ${token}` }
      })
        .then(res => res.json())
        .then(data => {
          if (Array.isArray(data)) setAuditLog(data);
        })
        .catch(err => console.error("Error fetching audit", err))
        .finally(() => setLoadingAudit(false));
    }
  }, [tab, request, token]);

  if (!request) return null;

  return (
    <div style={{
      position: "fixed", top: 0, right: 0, bottom: 0, width: 440,
      background: "#111827", borderLeft: "1px solid rgba(255,255,255,0.08)",
      display: "flex", flexDirection: "column", zIndex: 50,
      animation: "slideIn 0.2s ease",
    }}>
      {/* Header */}
      <div style={{ padding: "22px 24px 18px", borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <Avatar name={request.candidate_name} size={40} />
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: "#F9FAFB" }}>{request.candidate_name}</div>
              <div style={{ fontSize: 12, color: "#9CA3AF" }}>{request.candidate_email}</div>
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "none", border: "none", color: "#6B7280",
            cursor: "pointer", fontSize: 20, lineHeight: 1, padding: 4,
          }}>✕</button>
        </div>
        <div style={{ marginTop: 14 }}>
          <StateBadge state={request.state} />
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
        {["overview", "audit"].map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            flex: 1, padding: "10px 0", border: "none", cursor: "pointer",
            background: "none", fontSize: 13, fontWeight: 600,
            color: tab === t ? "#818CF8" : "#6B7280",
            borderBottom: tab === t ? "2px solid #818CF8" : "2px solid transparent",
          }}>{t.charAt(0).toUpperCase() + t.slice(1)}</button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>
        {tab === "overview" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{
              background: "rgba(255,255,255,0.03)", borderRadius: 10,
              border: "1px solid rgba(255,255,255,0.07)", padding: 16,
              display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12,
            }}>
              <div><div style={{ fontSize: 10, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>Position</div>
                <div style={{ fontSize: 13, color: "#E5E7EB", fontWeight: 500 }}>{request.position_title}</div></div>
              <div><div style={{ fontSize: 10, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>Agent Loops</div>
                <div style={{ fontSize: 13, color: "#E5E7EB", fontWeight: 500 }}>{request.loop_count}</div></div>
              <div><div style={{ fontSize: 10, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>Last Updated</div>
                <div style={{ fontSize: 13, color: "#E5E7EB", fontWeight: 500 }}>{fmtDate(request.updated_at)}</div></div>
              <div><div style={{ fontSize: 10, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>Scheduled</div>
                <div style={{ fontSize: 13, color: request.scheduled_at ? "#34D399" : "#E5E7EB", fontWeight: 500 }}>
                  {request.scheduled_at ? fmtDate(request.scheduled_at) : "—"}
                </div></div>
            </div>

            {/* Actions */}
            <div>
              <div style={{ fontSize: 11, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 10 }}>Actions</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {request.state === "human_intervention" && (
                  <ActionButton label="✓ Mark as Resolved" color="#10B981" onClick={() => onAction(request.id, "resolve")} />
                )}
                {!["scheduled", "cancelled", "failed"].includes(request.state) && (
                  <ActionButton label="⚡ Manual Override" color="#F59E0B" onClick={() => onAction(request.id, "override")} />
                )}
                {!["scheduled", "cancelled", "failed", "human_intervention"].includes(request.state) && (
                  <ActionButton label="⚑ Escalate to Human" color="#8B5CF6" onClick={() => onAction(request.id, "escalate")} />
                )}
                {!["cancelled", "scheduled"].includes(request.state) && (
                  <ActionButton label="✕ Cancel Request" color="#EF4444" onClick={() => onAction(request.id, "cancel")} />
                )}
              </div>
            </div>
          </div>
        ) : (
          loadingAudit ? (
            <div style={{ padding: 20, color: "#9CA3AF", fontSize: 13 }}>Loading audit trail...</div>
          ) : (
            <AuditTimeline entries={auditLog.length ? auditLog : MOCK_AUDIT} />
          )
        )}
      </div>
    </div>
  );
}

function ActionButton({ label, color, onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: hover ? `${color}22` : `${color}11`,
        border: `1px solid ${color}44`,
        color: color, borderRadius: 8, padding: "9px 14px",
        fontSize: 13, fontWeight: 600, cursor: "pointer",
        textAlign: "left", transition: "all 0.15s ease",
      }}
    >{label}</button>
  );
}

// ─── New Request modal ─────────────────────────────────────────────────────
function NewRequestModal({ onClose, onSubmit }) {
  const [form, setForm] = useState({ candidate_name: "", candidate_email: "", position_title: "", auto_send: false });
  const update = (k, v) => setForm(p => ({ ...p, [k]: v }));

  const valid = form.candidate_name.trim() && form.candidate_email.includes("@") && form.position_title.trim();

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
    }}>
      <div style={{
        background: "#111827", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 16, padding: 28, width: 440, maxWidth: "90vw",
        animation: "popIn 0.18s ease",
      }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: "#F9FAFB", marginBottom: 22 }}>
          New Scheduling Request
        </div>

        {[
          { key: "candidate_name", label: "Candidate Name", type: "text", placeholder: "Jane Smith" },
          { key: "candidate_email", label: "Email Address", type: "email", placeholder: "jane@example.com" },
          { key: "position_title", label: "Position", type: "text", placeholder: "Senior Engineer" },
        ].map(({ key, label, type, placeholder }) => (
          <div key={key} style={{ marginBottom: 14 }}>
            <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#9CA3AF", marginBottom: 5 }}>{label}</label>
            <input
              type={type} placeholder={placeholder} value={form[key]}
              onChange={e => update(key, e.target.value)}
              style={{
                width: "100%", background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8,
                padding: "9px 12px", color: "#F9FAFB", fontSize: 14,
                outline: "none", boxSizing: "border-box",
              }}
            />
          </div>
        ))}

        <label style={{ display: "flex", alignItems: "center", gap: 9, cursor: "pointer", marginBottom: 22 }}>
          <input type="checkbox" checked={form.auto_send} onChange={e => update("auto_send", e.target.checked)} />
          <span style={{ fontSize: 13, color: "#9CA3AF" }}>Auto-send outreach email immediately</span>
        </label>

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={{
            background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
            color: "#9CA3AF", borderRadius: 8, padding: "9px 18px", cursor: "pointer", fontSize: 13,
          }}>Cancel</button>
          <button
            onClick={() => valid && onSubmit(form)}
            style={{
              background: valid ? "#6366F1" : "#374151",
              border: "none", color: valid ? "#fff" : "#6B7280",
              borderRadius: 8, padding: "9px 18px", cursor: valid ? "pointer" : "default",
              fontSize: 13, fontWeight: 600,
            }}
          >Create Request</button>
        </div>
      </div>
    </div>
  );
}

// ─── Emails Page ──────────────────────────────────────────────────
function EmailsPage({ token }) {
  const [emails, setEmails] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    if (!token) return;
    fetch("http://localhost:8000/api/v1/requests/emails/log", {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then(r => r.json())
      .then(data => { if (Array.isArray(data)) setEmails(data); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [token]);

  return (
    <div style={{ marginLeft: 220, padding: "32px", minHeight: "100vh", maxWidth: 860 }}>
      <div style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.03em" }}>Email Activity</h1>
        <p style={{ fontSize: 13, color: "#6B7280", marginTop: 2 }}>
          Every email the AI agent has sent to candidates
          {emails.length > 0 && <span style={{ marginLeft: 8, background: "rgba(99,102,241,0.15)", color: "#818CF8", borderRadius: 12, padding: "2px 10px", fontSize: 11, fontWeight: 700 }}>{emails.length} sent</span>}
        </p>
      </div>

      {loading ? (
        <div style={{ color: "#6B7280", fontSize: 14 }}>Loading emails...</div>
      ) : emails.length === 0 ? (
        <div style={{
          background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)",
          borderRadius: 14, padding: 48, textAlign: "center",
        }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>✉</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: "#D1D5DB" }}>No emails sent yet</div>
          <div style={{ fontSize: 13, color: "#6B7280", marginTop: 8 }}>
            Create a request with <strong>Auto-send</strong> enabled to trigger the agent.
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {emails.map(email => (
            <div
              key={email.id}
              style={{
                background: "rgba(255,255,255,0.025)",
                border: `1px solid ${expanded === email.id ? "rgba(99,102,241,0.4)" : "rgba(255,255,255,0.07)"}`,
                borderRadius: 12, overflow: "hidden",
                transition: "border-color 0.15s",
              }}
            >
              {/* Header row */}
              <div
                onClick={() => setExpanded(expanded === email.id ? null : email.id)}
                style={{
                  display: "flex", alignItems: "center", gap: 14,
                  padding: "14px 18px", cursor: "pointer",
                }}
              >
                <div style={{
                  width: 34, height: 34, borderRadius: "50%",
                  background: "rgba(59,130,246,0.15)", border: "1px solid rgba(59,130,246,0.3)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 14, flexShrink: 0,
                }}>✉</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "#F9FAFB", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {email.subject}
                  </div>
                  <div style={{ fontSize: 12, color: "#6B7280", marginTop: 2 }}>
                    To: <span style={{ color: "#9CA3AF" }}>{email.to}</span>
                  </div>
                </div>
                <div style={{ fontSize: 11, color: "#6B7280", whiteSpace: "nowrap" }}>
                  {fmtDate(email.sent_at)}
                </div>
                <div style={{ color: "#6B7280", fontSize: 14, transition: "transform 0.15s", transform: expanded === email.id ? "rotate(90deg)" : "none" }}>›</div>
              </div>

              {/* Expanded body */}
              {expanded === email.id && (
                <div style={{
                  borderTop: "1px solid rgba(255,255,255,0.06)",
                  padding: "16px 18px",
                  background: "rgba(0,0,0,0.15)",
                }}>
                  <div style={{
                    fontFamily: "'DM Mono', monospace", fontSize: 12,
                    color: "#D1D5DB", whiteSpace: "pre-wrap", lineHeight: 1.7,
                  }}>{email.body}</div>
                  {email.request_id && (
                    <div style={{ marginTop: 12, fontSize: 11, color: "#4B5563" }}>
                      Request ID: {email.request_id}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Calendar Page ─────────────────────────────────────────────────────────
function CalendarPage({ requests }) {
  const scheduled = requests.filter(r => r.state === "scheduled" && r.scheduled_at)
    .sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));

  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  // Build a simple week view — 7 days from today
  const today = new Date();
  const week = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() + i);
    return d;
  });

  const eventsOnDay = (day) => scheduled.filter(r => {
    const d = new Date(r.scheduled_at);
    return d.getUTCFullYear() === day.getFullYear() &&
      d.getUTCMonth() === day.getMonth() &&
      d.getUTCDate() === day.getDate();
  });

  return (
    <div style={{ marginLeft: 220, padding: "32px", minHeight: "100vh" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.03em" }}>Calendar</h1>
          <p style={{ fontSize: 13, color: "#6B7280", marginTop: 2 }}>Upcoming scheduled interviews</p>
        </div>
        <div style={{ fontSize: 13, color: "#6B7280", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8, padding: "7px 14px" }}>
          {months[today.getMonth()]} {today.getFullYear()}
        </div>
      </div>

      {/* Week strip */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 10, marginBottom: 28 }}>
        {week.map((day, i) => {
          const events = eventsOnDay(day);
          const isToday = i === 0;
          return (
            <div key={i} style={{
              background: isToday ? "rgba(99,102,241,0.12)" : "rgba(255,255,255,0.02)",
              border: `1px solid ${isToday ? "rgba(99,102,241,0.4)" : "rgba(255,255,255,0.07)"}`,
              borderRadius: 12, padding: "14px 12px", minHeight: 110,
            }}>
              <div style={{ fontSize: 11, color: isToday ? "#818CF8" : "#6B7280", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {days[day.getDay()]}
              </div>
              <div style={{ fontSize: 22, fontWeight: 800, color: isToday ? "#818CF8" : "#F9FAFB", marginTop: 2, marginBottom: 8 }}>
                {day.getDate()}
              </div>
              {events.length === 0 ? (
                <div style={{ fontSize: 11, color: "#374151" }}>No interviews</div>
              ) : events.map(r => (
                <div key={r.id} style={{
                  background: "rgba(16,185,129,0.15)", border: "1px solid rgba(16,185,129,0.3)",
                  borderRadius: 6, padding: "4px 7px", marginBottom: 4,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "#34D399" }}>
                    {String(new Date(r.scheduled_at).getUTCHours()).padStart(2, "0")}:{String(new Date(r.scheduled_at).getUTCMinutes()).padStart(2, "0")}
                  </div>
                  <div style={{ fontSize: 11, color: "#D1D5DB", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {r.candidate_name}
                  </div>
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {/* All upcoming interviews list */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12 }}>
          All Scheduled Interviews
        </div>
        {scheduled.length === 0 ? (
          <div style={{
            background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 12, padding: 40, textAlign: "center", color: "#6B7280",
          }}>
            <div style={{ fontSize: 32, marginBottom: 10 }}>◷</div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>No interviews scheduled yet</div>
            <div style={{ fontSize: 12, marginTop: 4 }}>Scheduled interviews will appear here once the agent books them</div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {scheduled.map(r => (
              <div key={r.id} style={{
                background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)",
                borderRadius: 12, padding: "16px 20px",
                display: "flex", alignItems: "center", gap: 16,
              }}>
                <div style={{
                  width: 44, height: 44, borderRadius: 10, flexShrink: 0,
                  background: "rgba(16,185,129,0.12)", border: "1px solid rgba(16,185,129,0.25)",
                  display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                }}>
                  <div style={{ fontSize: 9, color: "#34D399", fontWeight: 700, textTransform: "uppercase" }}>
                    {months[new Date(r.scheduled_at).getUTCMonth()]}
                  </div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: "#34D399", lineHeight: 1 }}>
                    {new Date(r.scheduled_at).getUTCDate()}
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#F9FAFB" }}>{r.candidate_name}</div>
                  <div style={{ fontSize: 12, color: "#6B7280", marginTop: 2 }}>{r.position_title}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#34D399" }}>
                    {String(new Date(r.scheduled_at).getUTCHours()).padStart(2, "0")}:{String(new Date(r.scheduled_at).getUTCMinutes()).padStart(2, "0")} UTC
                  </div>
                  <div style={{ fontSize: 11, color: "#6B7280", marginTop: 2 }}>{r.candidate_email}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Settings Page ──────────────────────────────────────────────────────────
function SettingsPage({ showToast }) {
  const [settings, setSettings] = useState({
    org_name: "Acme Corp",
    recruiter_email: "recruiter@acme.com",
    max_agent_loops: 3,
    auto_escalate: true,
    send_candidate_reminders: true,
    primary_llm: "gpt-4o",
    timezone: "UTC",
    slack_webhook: "",
    email_from: "interviews@acme.com",
    google_connected: false,
    microsoft_connected: false,
  });

  // Load real calendar connection status from API on mount (survives refresh)
  useEffect(() => {
    fetch("http://localhost:8000/auth/calendar-status")
      .then(r => r.json())
      .then(data => {
        if (data.google !== undefined) {
          setSettings(p => ({ ...p, google_connected: data.google, microsoft_connected: data.microsoft }));
        }
      })
      .catch(() => { }); // Fail silently — user just sees "not connected"
  }, []);

  // Detect OAuth success redirect from Google
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("connected") === "google") {
      setSettings(p => ({ ...p, google_connected: true }));
      showToast("Google Calendar connected ✓", "#10B981");
      // Clean the URL
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const connectGoogle = () => {
    const email = settings.recruiter_email || "recruiter@test.com";
    window.location.href = `http://localhost:8000/auth/google?interviewer_email=${encodeURIComponent(email)}`;
  };

  const update = (k, v) => setSettings(p => ({ ...p, [k]: v }));

  const Section = ({ title, children }) => (
    <div style={{ marginBottom: 28 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 14 }}>
        {title}
      </div>
      <div style={{
        background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)",
        borderRadius: 14, overflow: "hidden",
      }}>
        {children}
      </div>
    </div>
  );

  const Row = ({ label, sub, children }) => (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "14px 18px", borderBottom: "1px solid rgba(255,255,255,0.05)",
    }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#E5E7EB" }}>{label}</div>
        {sub && <div style={{ fontSize: 11, color: "#6B7280", marginTop: 2 }}>{sub}</div>}
      </div>
      <div style={{ flexShrink: 0, marginLeft: 20 }}>{children}</div>
    </div>
  );

  const Toggle = ({ value, onChange }) => (
    <div onClick={() => onChange(!value)} style={{
      width: 40, height: 22, borderRadius: 11, cursor: "pointer",
      background: value ? "#6366F1" : "rgba(255,255,255,0.1)",
      position: "relative", transition: "background 0.2s",
      border: `1px solid ${value ? "#818CF8" : "rgba(255,255,255,0.15)"}`,
    }}>
      <div style={{
        width: 16, height: 16, borderRadius: "50%", background: "#fff",
        position: "absolute", top: 2, left: value ? 20 : 2,
        transition: "left 0.2s", boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
      }} />
    </div>
  );

  const Input = ({ value, onChange, placeholder, type = "text" }) => (
    <input
      type={type} value={value} placeholder={placeholder}
      onChange={e => onChange(e.target.value)}
      style={{
        background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 7, padding: "7px 11px", color: "#F9FAFB", fontSize: 13,
        outline: "none", width: 200,
      }}
    />
  );

  const Select = ({ value, onChange, options }) => (
    <select value={value} onChange={e => onChange(e.target.value)} style={{
      background: "#1F2937", border: "1px solid rgba(255,255,255,0.1)",
      borderRadius: 7, padding: "7px 11px", color: "#F9FAFB", fontSize: 13,
      outline: "none", cursor: "pointer",
    }}>
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );

  return (
    <div style={{ marginLeft: 220, padding: "32px", minHeight: "100vh", maxWidth: 780 }}>
      <div style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.03em" }}>Settings</h1>
        <p style={{ fontSize: 13, color: "#6B7280", marginTop: 2 }}>Configure your organisation and agent behaviour</p>
      </div>

      <Section title="Organisation">
        <Row label="Organisation Name" sub="Displayed in outreach emails">
          <Input value={settings.org_name} onChange={v => update("org_name", v)} placeholder="Acme Corp" />
        </Row>
        <Row label="Recruiter Email" sub="Replies and escalations go here">
          <Input value={settings.recruiter_email} onChange={v => update("recruiter_email", v)} placeholder="you@company.com" type="email" />
        </Row>
        <Row label="From Email" sub="Sender address for outbound emails">
          <Input value={settings.email_from} onChange={v => update("email_from", v)} placeholder="interviews@company.com" />
        </Row>
        <Row label="Timezone" sub="Used for all scheduling logic">
          <Select value={settings.timezone} onChange={v => update("timezone", v)} options={[
            { value: "UTC", label: "UTC" },
            { value: "America/New_York", label: "US Eastern" },
            { value: "America/Los_Angeles", label: "US Pacific" },
            { value: "Europe/London", label: "London" },
            { value: "Asia/Kolkata", label: "India (IST)" },
            { value: "Asia/Singapore", label: "Singapore" },
          ]} />
        </Row>
      </Section>

      <Section title="Agent Behaviour">
        <Row label="Max Agent Loops" sub="Escalate to human after this many attempts">
          <Select value={String(settings.max_agent_loops)} onChange={v => update("max_agent_loops", Number(v))} options={[
            { value: "2", label: "2 loops" },
            { value: "3", label: "3 loops" },
            { value: "4", label: "4 loops" },
            { value: "5", label: "5 loops" },
          ]} />
        </Row>
        <Row label="Auto-escalate on anger" sub="Detect negative sentiment and escalate automatically">
          <Toggle value={settings.auto_escalate} onChange={v => update("auto_escalate", v)} />
        </Row>
        <Row label="Send candidate reminders" sub="24h reminder email before interview">
          <Toggle value={settings.send_candidate_reminders} onChange={v => update("send_candidate_reminders", v)} />
        </Row>
        <Row label="Primary LLM Model" sub="Fallback is always Claude 3.5 Sonnet">
          <Select value={settings.primary_llm} onChange={v => update("primary_llm", v)} options={[
            { value: "gpt-4o", label: "GPT-4o" },
            { value: "gpt-4-turbo", label: "GPT-4 Turbo" },
            { value: "claude-3-5-sonnet-20241022", label: "Claude 3.5 Sonnet" },
          ]} />
        </Row>
      </Section>

      <Section title="Calendar Integrations">
        <Row label="Google Calendar" sub={settings.google_connected ? "Connected — real slots enabled ✓" : "Not connected — using availability simulation"}>
          <button onClick={() => {
            if (settings.google_connected) {
              update("google_connected", false);
              showToast("Google disconnected", "#EF4444");
            } else {
              connectGoogle();
            }
          }} style={{
            background: settings.google_connected ? "rgba(239,68,68,0.1)" : "rgba(99,102,241,0.15)",
            border: `1px solid ${settings.google_connected ? "rgba(239,68,68,0.3)" : "rgba(99,102,241,0.3)"}`,
            color: settings.google_connected ? "#F87171" : "#818CF8",
            borderRadius: 7, padding: "7px 14px", fontSize: 12, fontWeight: 600, cursor: "pointer",
          }}>{settings.google_connected ? "Disconnect" : "Connect Google"}</button>
        </Row>
        <Row label="Microsoft 365" sub={settings.microsoft_connected ? "Connected ✓" : "Not connected"}>
          <button onClick={() => { update("microsoft_connected", !settings.microsoft_connected); showToast(settings.microsoft_connected ? "Microsoft disconnected" : "Microsoft 365 connected ✓", settings.microsoft_connected ? "#EF4444" : "#10B981"); }} style={{
            background: settings.microsoft_connected ? "rgba(239,68,68,0.1)" : "rgba(99,102,241,0.15)",
            border: `1px solid ${settings.microsoft_connected ? "rgba(239,68,68,0.3)" : "rgba(99,102,241,0.3)"}`,
            color: settings.microsoft_connected ? "#F87171" : "#818CF8",
            borderRadius: 7, padding: "7px 14px", fontSize: 12, fontWeight: 600, cursor: "pointer",
          }}>{settings.microsoft_connected ? "Disconnect" : "Connect Microsoft"}</button>
        </Row>
      </Section>

      <Section title="Notifications">
        <Row label="Slack Webhook URL" sub="Get escalation alerts in Slack">
          <Input value={settings.slack_webhook} onChange={v => update("slack_webhook", v)} placeholder="https://hooks.slack.com/..." />
        </Row>
      </Section>

      {/* Save button */}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
        <button onClick={() => showToast("Settings saved ✓")} style={{
          background: "#6366F1", border: "none", color: "#fff",
          borderRadius: 9, padding: "10px 24px", fontSize: 13, fontWeight: 700,
          cursor: "pointer",
        }}>Save Settings</button>
      </div>
    </div>
  );
}

// ─── Main Dashboard ────────────────────────────────────────────────────────
export default function Dashboard() {
  const [requests, setRequests] = useState([]);
  const [selected, setSelected] = useState(null);
  const [filterState, setFilterState] = useState("all");
  const [search, setSearch] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [toast, setToast] = useState(null);
  const [activePage, setActivePage] = useState("Dashboard");
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  const showToast = (msg, color = "#10B981") => {
    setToast({ msg, color });
    setTimeout(() => setToast(null), 3000);
  };

  useEffect(() => {
    fetch("http://localhost:8000/auth/dev/token")
      .then(res => res.json())
      .then(data => setToken(data.access_token))
      .catch(console.error);
  }, []);

  const fetchRequests = useCallback(() => {
    if (!token) return;
    setLoading(true);
    fetch("http://localhost:8000/api/v1/requests/", {
      headers: { "Authorization": `Bearer ${token}` }
    })
      .then(res => res.json())
      .then(data => {
        if (data.items) setRequests(data.items);
      })
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => { fetchRequests(); }, [fetchRequests]);

  // Stats
  const stats = {
    total: requests.length,
    negotiating: requests.filter(r => r.state === "negotiating").length,
    scheduled: requests.filter(r => r.state === "scheduled").length,
    needsReview: requests.filter(r => r.state === "human_intervention").length,
  };

  // Filtered list
  const filtered = requests.filter(r => {
    const matchState = filterState === "all" || r.state === filterState;
    const matchSearch = !search ||
      r.candidate_name.toLowerCase().includes(search.toLowerCase()) ||
      r.candidate_email.toLowerCase().includes(search.toLowerCase()) ||
      r.position_title.toLowerCase().includes(search.toLowerCase());
    return matchState && matchSearch;
  });

  const handleAction = async (id, action) => {
    if (!token) return;
    try {
      let endpoint = `http://localhost:8000/api/v1/requests/${id}/${action}`;
      let bodyData = null;
      let method = "POST";

      if (action === "cancel") {
        method = "PATCH";
      } else if (action === "escalate") {
        bodyData = { reason: "Recruiter initiated escalation via Dashboard" };
      } else if (action === "override") {
        bodyData = { scheduled_at: new Date(Date.now() + 86400000 * 3).toISOString() };
      }

      const res = await fetch(endpoint, {
        method,
        headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
        body: bodyData ? JSON.stringify(bodyData) : null
      });

      if (res.ok) {
        const messages = { cancel: "Request cancelled", escalate: "Escalated to human", override: "Manually scheduled ✓", resolve: "Returned to negotiation" };
        const colors = { cancel: "#EF4444", escalate: "#8B5CF6", override: "#10B981", resolve: "#3B82F6" };
        showToast(messages[action], colors[action]);
        setSelected(null);
        fetchRequests();
      } else {
        const errData = await res.json();
        showToast(`Error: ${errData.detail || "Action failed"}`, "#EF4444");
      }
    } catch (err) {
      console.error(err);
      showToast("Network error", "#EF4444");
    }
  };

  const handleCreate = async (form) => {
    if (!token) return;
    try {
      const res = await fetch("http://localhost:8000/api/v1/requests/", {
        method: "POST",
        headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(form)
      });
      if (res.ok) {
        setShowModal(false);
        showToast("Request created successfully");
        fetchRequests();
      } else {
        const errData = await res.json();
        showToast(`Error: ${errData.detail || "Creation failed"}`, "#EF4444");
      }
    } catch (err) {
      console.error(err);
      showToast("Network error", "#EF4444");
    }
  };

  const stateFilters = ["all", "draft", "outreach_sent", "negotiating", "scheduled", "human_intervention", "failed"];

  return (
    <div suppressHydrationWarning style={{
      minHeight: "100vh", background: "#0B0F19",
      fontFamily: "'DM Sans', 'Inter', system-ui, sans-serif",
      color: "#F9FAFB",
    }}>
      <style suppressHydrationWarning>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500;700&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
        @keyframes slideIn { from { transform: translateX(30px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        @keyframes popIn  { from { transform: scale(0.95); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        @keyframes fadeUp { from { transform: translateY(6px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        tr:hover td { background: rgba(255,255,255,0.025) !important; }
      `}</style>

      {/* Sidebar */}
      <div style={{
        position: "fixed", left: 0, top: 0, bottom: 0, width: 220,
        background: "rgba(255,255,255,0.02)",
        borderRight: "1px solid rgba(255,255,255,0.06)",
        display: "flex", flexDirection: "column", padding: "24px 0",
      }}>
        <div style={{ padding: "0 20px 28px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{
              width: 30, height: 30, borderRadius: 8,
              background: "linear-gradient(135deg, #6366F1, #8B5CF6)",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 14,
            }}>⟳</div>
            <span style={{ fontSize: 15, fontWeight: 700, letterSpacing: "-0.02em" }}>ScheduleAI</span>
          </div>
        </div>

        {[
          { icon: "⊞", label: "Dashboard" },
          { icon: "✉", label: "Emails" },
          { icon: "◷", label: "Calendar" },
          { icon: "⚙", label: "Settings" },
        ].map(item => (
          <div key={item.label} onClick={() => setActivePage(item.label)} style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "9px 20px", cursor: "pointer",
            background: activePage === item.label ? "rgba(99,102,241,0.12)" : "none",
            borderLeft: activePage === item.label ? "2px solid #6366F1" : "2px solid transparent",
            color: activePage === item.label ? "#818CF8" : "#6B7280",
            fontSize: 13, fontWeight: activePage === item.label ? 600 : 400,
            transition: "all 0.15s ease",
          }}>
            <span style={{ fontSize: 15 }}>{item.icon}</span>
            {item.label}
          </div>
        ))}

        <div style={{ flex: 1 }} />

        {/* Escalation alert */}
        {stats.needsReview > 0 && (
          <div style={{
            margin: "0 12px 16px",
            background: "rgba(139,92,246,0.12)", border: "1px solid rgba(139,92,246,0.25)",
            borderRadius: 10, padding: "10px 12px",
          }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#A78BFA" }}>⚑ Needs Review</div>
            <div style={{ fontSize: 11, color: "#7C3AED", marginTop: 2 }}>{stats.needsReview} request{stats.needsReview > 1 ? "s" : ""} escalated</div>
          </div>
        )}
      </div>

      {/* Page Router — render correct page based on activePage */}
      {activePage === "Calendar" && <CalendarPage requests={requests} />}
      {activePage === "Settings" && <SettingsPage showToast={showToast} />}
      {activePage === "Emails" && <EmailsPage token={token} />}

      {/* Main content — Dashboard page only */}
      {activePage === "Dashboard" && (
        <div style={{ marginLeft: 220, padding: "32px 32px 32px", minHeight: "100vh" }}>
          {/* Top bar */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 28 }}>
            <div>
              <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.03em" }}>Scheduling Requests</h1>
              <p style={{ fontSize: 13, color: "#6B7280", marginTop: 2 }}>AI-managed interview coordination</p>
            </div>
            <button
              onClick={() => setShowModal(true)}
              style={{
                background: "#6366F1", border: "none", color: "#fff",
                borderRadius: 9, padding: "10px 18px", fontSize: 13, fontWeight: 600,
                cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
              }}
            >
              <span style={{ fontSize: 16, lineHeight: 1 }}>+</span> New Request
            </button>
          </div>

          {/* Metrics */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 28 }}>
            <MetricCard label="Total" value={stats.total} />
            <MetricCard label="Negotiating" value={stats.negotiating} accent="#FCD34D" />
            <MetricCard label="Scheduled" value={stats.scheduled} accent="#34D399" />
            <MetricCard label="Needs Review" value={stats.needsReview} accent="#A78BFA" />
          </div>

          {/* Filters + Search */}
          <div style={{ display: "flex", gap: 12, marginBottom: 18, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ position: "relative", flex: "1", maxWidth: 300 }}>
              <span style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "#6B7280", fontSize: 14 }}>⌕</span>
              <input
                placeholder="Search candidate, email, role…"
                value={search}
                onChange={e => setSearch(e.target.value)}
                style={{
                  width: "100%", paddingLeft: 32, paddingRight: 12, paddingTop: 8, paddingBottom: 8,
                  background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.09)",
                  borderRadius: 8, color: "#E5E7EB", fontSize: 13, outline: "none",
                }}
              />
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {stateFilters.map(s => {
                const active = filterState === s;
                const label = s === "all" ? "All" : (STATE_CONFIG[s]?.label || s);
                return (
                  <button key={s} onClick={() => setFilterState(s)} style={{
                    padding: "6px 12px", borderRadius: 20, border: "none",
                    background: active ? "rgba(99,102,241,0.25)" : "rgba(255,255,255,0.04)",
                    color: active ? "#818CF8" : "#6B7280",
                    fontSize: 12, fontWeight: 600, cursor: "pointer",
                    border: active ? "1px solid rgba(99,102,241,0.4)" : "1px solid rgba(255,255,255,0.07)",
                  }}>{label}</button>
                );
              })}
            </div>
          </div>

          {/* Table */}
          <div style={{
            background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 14, overflow: "hidden",
          }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
                  {["Candidate", "Position", "Status", "Loops", "Scheduled", "Updated", ""].map(h => (
                    <th key={h} style={{
                      padding: "11px 16px", textAlign: "left",
                      fontSize: 11, fontWeight: 600, color: "#6B7280",
                      textTransform: "uppercase", letterSpacing: "0.06em",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={7} style={{ padding: 40, textAlign: "center", color: "#6B7280", fontSize: 14 }}>No requests match your filters</td></tr>
                ) : filtered.map((r, i) => (
                  <tr
                    key={r.id}
                    onClick={() => setSelected(r)}
                    style={{
                      borderBottom: i < filtered.length - 1 ? "1px solid rgba(255,255,255,0.04)" : "none",
                      cursor: "pointer",
                      animation: `fadeUp 0.2s ease ${i * 0.03}s both`,
                    }}
                  >
                    <td style={{ padding: "13px 16px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <Avatar name={r.candidate_name} size={30} />
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 600, color: "#F9FAFB" }}>{r.candidate_name}</div>
                          <div style={{ fontSize: 11, color: "#6B7280" }}>{r.candidate_email}</div>
                        </div>
                      </div>
                    </td>
                    <td style={{ padding: "13px 16px", fontSize: 13, color: "#D1D5DB" }}>{r.position_title}</td>
                    <td style={{ padding: "13px 16px" }}><StateBadge state={r.state} /></td>
                    <td style={{ padding: "13px 16px" }}>
                      <span style={{
                        fontSize: 12, fontFamily: "'DM Mono', monospace",
                        color: r.loop_count >= 3 ? "#F87171" : "#9CA3AF",
                      }}>{r.loop_count}</span>
                    </td>
                    <td style={{ padding: "13px 16px", fontSize: 12, color: r.scheduled_at ? "#34D399" : "#4B5563" }}>
                      {r.scheduled_at ? fmtDate(r.scheduled_at) : "—"}
                    </td>
                    <td style={{ padding: "13px 16px", fontSize: 12, color: "#6B7280" }}>{fmtRelative(r.updated_at)}</td>
                    <td style={{ padding: "13px 16px" }}>
                      <span style={{ color: "#6B7280", fontSize: 16 }}>›</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ marginTop: 12, fontSize: 12, color: "#4B5563", textAlign: "right" }}>
            {filtered.length} of {requests.length} requests
          </div>
        </div>
      )} {/* end Dashboard page */}


      {/* Side panel */}
      {selected && (
        <DetailPanel
          request={requests.find(r => r.id === selected.id) || selected}
          onClose={() => setSelected(null)}
          onAction={handleAction}
          token={token}
        />
      )}

      {/* Modal */}
      {showModal && (
        <NewRequestModal onClose={() => setShowModal(false)} onSubmit={handleCreate} />
      )}

      {/* Toast */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 28, left: "50%", transform: "translateX(-50%)",
          background: toast.color, color: "#fff",
          borderRadius: 10, padding: "10px 20px", fontSize: 13, fontWeight: 600,
          boxShadow: "0 4px 20px rgba(0,0,0,0.4)", zIndex: 200,
          animation: "popIn 0.15s ease",
        }}>{toast.msg}</div>
      )}
    </div>
  );
}
