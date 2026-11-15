import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import type { TicketList, TicketRecord } from "../types";

type Filter = "open" | "closed" | "all";

function duration(minutes: number): string {
  const m = Math.round(Math.abs(minutes));          // round first, so 239.9 min reads "4 h", not "3 h 60 min"
  if (m < 1) return "under a minute";
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h < 24) return rest ? `${h} h ${rest} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return `${d} d ${h % 24} h`;
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

// Response time: open tickets count down to their SLA; closed ones show how long they took.
function ResponseTime({ t }: { t: TicketRecord }) {
  if (t.status === "closed") {
    return (
      <div>
        <span className={`badge ${t.within_sla ? "ok" : "bad"}`}>{t.within_sla ? "Within SLA" : "SLA missed"}</span>
        <div className="muted small">Closed in {duration(t.age_minutes)} · SLA {t.sla_hours} h</div>
      </div>
    );
  }
  const left = t.minutes_left ?? 0;
  return (
    <div>
      <span className={`badge ${left < 0 ? "bad" : left < 30 ? "warn" : "ok"}`}>
        {left < 0 ? `Overdue by ${duration(left)}` : `${duration(left)} left`}
      </span>
      <div className="muted small">Due {when(t.due)} · SLA {t.sla_hours} h</div>
    </div>
  );
}

export default function TicketsPage({ onCounts }: { onCounts: (open: number) => void }) {
  const [filter, setFilter] = useState<Filter>("open");
  const [data, setData] = useState<TicketList | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(f: Filter = filter) {
    setError(null);
    try {
      const d = await api.tickets(f);
      setData(d);
      onCounts(d.counts.open);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load(filter);
    const timer = setInterval(() => load(filter), 30000);   // keep the countdowns current
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  async function setStatus(t: TicketRecord, status: "open" | "closed") {
    setBusy(t.ticket_id);
    try {
      await api.setTicketStatus(t.ticket_id, status);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  // Open first, then by priority (P1 first), then the soonest due.
  const rows = [...(data?.tickets ?? [])].sort((a, b) =>
    a.status !== b.status ? (a.status === "open" ? -1 : 1)
      : a.priority !== b.priority ? a.priority.localeCompare(b.priority)
        : a.due - b.due);
  const overdue = (data?.tickets ?? []).filter((t) => t.status === "open" && (t.minutes_left ?? 0) < 0).length;

  return (
    <div className="tickets-page">
      <section className="card">
        <div className="run-head">
          <div>
            <h2 className="card-title">Support tickets</h2>
            <p className="muted small">
              Opened when a user asks for a person, is angry, or keeps hitting the safety policy. All text is
              masked: personal data never appears here.
            </p>
          </div>
          <button className="secondary" onClick={() => load()}>Refresh</button>
        </div>
        <div className="ticket-stats">
          <div className="metric"><span className="metric-value">{data?.counts.open ?? "–"}</span><span className="muted small">open</span></div>
          <div className="metric"><span className={`metric-value ${overdue ? "bad-text" : ""}`}>{data ? overdue : "–"}</span><span className="muted small">overdue</span></div>
          <div className="metric"><span className="metric-value">{data?.counts.closed ?? "–"}</span><span className="muted small">closed</span></div>
          <div className="segmented" role="tablist" aria-label="Filter tickets">
            {(["open", "closed", "all"] as Filter[]).map((f) => (
              <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? "active" : ""}
                      onClick={() => setFilter(f)}>
                {f === "all" ? "All" : f === "open" ? "Open" : "Closed"}
              </button>
            ))}
          </div>
        </div>
        {error && <div className="callout bad compact">{error}</div>}
        {data && rows.length === 0 && (
          <p className="muted empty-tickets">No {filter === "all" ? "" : filter} tickets.</p>
        )}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table className="table tickets">
              <thead>
                <tr>
                  <th>Ticket</th><th>Priority</th><th>User</th><th>Reason</th><th>Opened</th>
                  <th>Response time</th><th>Status</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <Fragment key={t.ticket_id}>
                    <tr className={open === t.ticket_id ? "expanded" : ""}>
                      <td>
                        <button className="link-btn" onClick={() => setOpen(open === t.ticket_id ? null : t.ticket_id)}
                                aria-expanded={open === t.ticket_id}>
                          {open === t.ticket_id ? "▾" : "▸"} {t.ticket_id}
                        </button>
                      </td>
                      <td><span className={`badge ${t.priority === "P1" ? "bad" : t.priority === "P2" ? "warn" : "muted"}`}>{t.priority}</span></td>
                      <td>{t.user_id}</td>
                      <td className="reason-cell">{t.reason}</td>
                      <td className="small">{when(t.created)}</td>
                      <td><ResponseTime t={t} /></td>
                      <td>
                        <span className={`badge ${t.status === "open" ? "warn" : "ok"}`}>{t.status === "open" ? "Open" : "Closed"}</span>
                        {t.closed_by && <div className="muted small">by {t.closed_by}</div>}
                      </td>
                      <td>
                        {t.status === "open" ? (
                          <button className="primary small" disabled={busy === t.ticket_id} onClick={() => setStatus(t, "closed")}>Close</button>
                        ) : (
                          <button className="secondary small" disabled={busy === t.ticket_id} onClick={() => setStatus(t, "open")}>Reopen</button>
                        )}
                      </td>
                    </tr>
                    {open === t.ticket_id && (
                      <tr className="ticket-detail">
                        <td colSpan={8}>
                          <div className="detail-grid">
                            <div>
                              <h3 className="section-title">Handoff note</h3>
                              <pre className="report">{t.handoff_note}</pre>
                            </div>
                            <div>
                              <h3 className="section-title">User's request (personal data masked)</h3>
                              <pre className="report">{t.request}</pre>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
