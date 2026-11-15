import { useState } from "react";
import type { HistoryEntry } from "../types";
import CodeBlock from "./CodeBlock";
import RunPanel from "./RunPanel";
import AccessDenied from "./AccessDenied";
import { LANGUAGE, PII_LABEL, statusInfo, steps, TIER } from "./status";
import type { PiiFinding } from "../types";

type Tab = "answer" | "overview" | "code" | "tests" | "run" | "results" | "ticket";

function PrivacyCard({ pii }: { pii: PiiFinding[] }) {
  if (!pii.length) return null;
  return (
    <section className="privacy">
      <div className="privacy-head">
        <span className="privacy-icon" aria-hidden>🔒</span>
        <div>
          <h3>Sensitive data was masked</h3>
          <p className="muted small">
            These values were replaced before any model, log or ticket saw them. The checks below ran in code, not in the model.
          </p>
        </div>
      </div>
      <ul className="privacy-list">
        {pii.map((f) => (
          <li key={f.placeholder}>
            <span className="privacy-type">{PII_LABEL[f.type] ?? f.type}</span>
            <code>{f.placeholder}</code>
            <span className={`badge ${f.valid === true ? "ok" : f.valid === false ? "bad" : "muted"}`}>
              {f.valid === true ? "Valid format" : f.valid === false ? "Invalid" : "Masked"}
            </span>
            <span className="privacy-detail">{f.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface Props {
  entry: HistoryEntry;
  userId: string;
  role: string | undefined;
  busy: boolean;
  onDecide: (d: "approve" | "reject") => void;
}

export default function RequestView({ entry, userId, role, busy, onDecide }: Props) {
  const r = entry.reply;
  const s = statusInfo(r.status);
  const tier = TIER[r.tier] ?? { label: r.tier, help: "" };

  const lang = LANGUAGE[r.language ?? "python"];
  const tabs: { id: Tab; label: string; show: boolean; mark?: string }[] = [
    { id: "answer", label: "Answer", show: !!r.answer },
    { id: "overview", label: "Overview", show: true },
    { id: "code", label: r.sql ? "Query" : "Code", show: !!(r.code || r.sql) },
    { id: "tests", label: "Tests", show: !!r.tests },
    { id: "run", label: "Run", show: !!r.code, mark: r.status === "done" ? "ready" : undefined },
    {
      id: "results", label: "Test results", show: !!r.run,
      mark: r.run ? (r.run.exit_code === 0 ? "pass" : "fail") : undefined,
    },
    { id: "ticket", label: "Ticket", show: !!r.ticket, mark: r.ticket?.priority },
  ];
  const visible = tabs.filter((t) => t.show);
  const [tab, setTab] = useState<Tab>(r.answer ? "answer" : "overview");
  const active = visible.some((t) => t.id === tab) ? tab : "overview";

  if ((r.status === "access_denied" || r.status === "quota_exceeded") && r.denial) {
    return (
      <div className="request">
        <section className="card request-head">
          <div className="request-title">
            <div className="eyebrow">Request</div>
            <h1>{r.request}</h1>
          </div>
          <div className="request-badges">
            <span className={`badge lg ${s.tone}`}>{s.label}</span>
          </div>
        </section>
        <AccessDenied denial={r.denial} />
      </div>
    );
  }

  return (
    <div className="request">
      <section className="card request-head">
        <div className="request-title">
          <div className="eyebrow">Request</div>
          <h1>{r.request}</h1>
        </div>
        <div className="request-badges">
          <span className={`badge lg ${s.tone}`}>{s.label}</span>
          {r.language && <span className="badge lg muted">{lang.label}</span>}
          <span className="badge lg muted" title={tier.help}>{tier.label}</span>
        </div>
        <ol className="stepper" aria-label="Progress">
          {steps(r).map((st, i) => (
            <li key={i} className={`step ${st.state}`}>
              <span className="step-icon" aria-hidden>
                {st.state === "done" ? "✓" : st.state === "failed" ? "✕" : st.state === "warn" ? "!" : i + 1}
              </span>
              <span className="step-text">
                <span className="step-label">{st.label}</span>
                <span className="step-note">{st.note}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      {r.status === "awaiting_approval" && (
        <section className="callout warn">
          <div>
            <h3>Approval needed before this code can run</h3>
            <p>The safety check found file, OS or network access. Review the code in the Code tab.</p>
            <ul>{(r.policy?.reasons ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul>
            {role !== "admin" && <p className="muted small">Only an admin can decide. Switch “Signed in as” to Admin.</p>}
          </div>
          <div className="callout-actions">
            <button className="primary" disabled={busy || role !== "admin"} onClick={() => onDecide("approve")}>
              Approve and run tests
            </button>
            <button className="secondary" disabled={busy || role !== "admin"} onClick={() => onDecide("reject")}>
              Reject
            </button>
          </div>
        </section>
      )}
      {r.status !== "done" && r.status !== "awaiting_approval" && r.status !== "sql" && r.status !== "answered" && (
        <section className={`callout ${s.tone}`}>
          <div>
            <h3>{s.label}</h3>
            <p>{r.status === "access_denied" && r.report ? r.report : s.help}</p>
          </div>
        </section>
      )}

      <section className="card tabs-card">
        <div className="tabs" role="tablist">
          {visible.map((t) => (
            <button key={t.id} role="tab" aria-selected={active === t.id} className={active === t.id ? "active" : ""}
                    onClick={() => setTab(t.id)}>
              {t.label}
              {t.mark && <span className={`tab-mark ${t.mark}`}>{t.mark}</span>}
            </button>
          ))}
        </div>

        <div className="tab-body" role="tabpanel">
          {active === "answer" && r.answer && (
            <div className="overview">
              {/* Model text is shown as plain text (pre-wrap), never as HTML. */}
              <div className="answer">{r.answer}</div>
              <PrivacyCard pii={r.pii ?? []} />
            </div>
          )}
          {active === "overview" && (
            <div className="overview">
              <dl className="facts">
                <div><dt>Status</dt><dd><span className={`badge ${s.tone}`}>{s.label}</span><p>{s.help}</p></dd></div>
                <div><dt>Answered by</dt><dd><strong>{tier.label}</strong><p>{tier.help}</p></dd></div>
                {r.policy && (
                  <div>
                    <dt>Safety check</dt>
                    <dd>
                      <strong>{{ allow: "Passed", needs_approval: "Needs approval", deny: "Blocked" }[r.policy.decision]}</strong>
                      <ul>{r.policy.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
                    </dd>
                  </div>
                )}
                {r.run && (
                  <div>
                    <dt>Tests</dt>
                    <dd>
                      <strong>
                        {r.run.timed_out ? "Timed out" : r.run.exit_code === 0 ? "All tests passed" : "Some tests failed"}
                      </strong>
                      <p>Run by {lang.runner} in the sandbox{r.run.cached ? " (same code as an earlier run, result reused)" : ""}.</p>
                    </dd>
                  </div>
                )}
              </dl>
              {!r.answer && <PrivacyCard pii={r.pii ?? []} />}
              <h3 className="section-title">Report</h3>
              <pre className="report">{r.report}</pre>
              {r.status === "done" && r.code && (
                <button className="primary" onClick={() => setTab("run")}>Try it with your own input →</button>
              )}
            </div>
          )}
          {active === "code" && (r.code || r.sql) && (
            <CodeBlock code={(r.code ?? r.sql)!} title={r.sql ? "query.sql" : lang.file} lang={r.sql ? "sql" : lang.label} />
          )}
          {active === "tests" && r.tests && (
            <>
              <p className="muted small tab-intro">Tests the assistant wrote for this code. They ran in the sandbox with {lang.runner}.</p>
              <CodeBlock code={r.tests} title={lang.tests} />
            </>
          )}
          {active === "run" && r.code && <RunPanel reply={r} userId={userId} />}
          {active === "results" && r.run && (
            <>
              <p className="muted small tab-intro">
                {lang.runner} {r.run.timed_out ? "was stopped at the time limit" : `finished with exit code ${r.run.exit_code}`}.
                Sandbox: {r.run.sandbox}.
              </p>
              <pre className="console">{r.run.output || "(no output)"}</pre>
            </>
          )}
          {active === "ticket" && r.ticket && (
            <div className="overview">
              <dl className="facts">
                <div><dt>Ticket</dt><dd><strong>{r.ticket.ticket_id}</strong></dd></div>
                <div><dt>Priority</dt><dd><span className={`badge ${r.ticket.priority === "P1" ? "bad" : "warn"}`}>{r.ticket.priority}</span><p>Response within {r.ticket.sla_hours} h</p></dd></div>
                <div><dt>Reason</dt><dd>{r.ticket.reason}</dd></div>
              </dl>
              <h3 className="section-title">Handoff note</h3>
              <pre className="report">{r.ticket.handoff_note}</pre>
              <h3 className="section-title">Request (sensitive data masked)</h3>
              <pre className="report">{r.ticket.request}</pre>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
