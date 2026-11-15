import type { RuleResult, RulesReport } from "../types";

const STATUS: Record<RuleResult["status"], { label: string; tone: string }> = {
  pass: { label: "Pass", tone: "ok" },
  fail: { label: "Fail", tone: "bad" },
  approval: { label: "Needs approval", tone: "warn" },
  "n/a": { label: "n/a", tone: "muted" },
};

const pct = (x: number) => `${Math.round(x * 100)}%`;

function RuleTable({ rows, soft }: { rows: RuleResult[]; soft?: boolean }) {
  if (!rows.length) return null;
  return (
    <div className="table-wrap">
      <table className="table rules-table">
        <thead>
          <tr><th>Rule</th><th>Stage</th><th>Result</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id + r.stage} className={r.status === "fail" ? "rule-failed" : ""}>
              <td><strong>{r.name}</strong><div className="muted small mono">{r.id}</div></td>
              <td>{r.stage === "prompt" ? "Prompt" : "Code"}</td>
              <td><span className={`badge ${STATUS[r.status].tone}`}>{STATUS[r.status].label}</span></td>
              <td>
                {r.detail}
                {soft && r.tip && <div className="muted small">Tip: {r.tip}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Every response shows the central rules it went through: hard rules (all required, checked before any
// model call) and soft rules (advice, target 85%).
export default function RulesPanel({ rules }: { rules: RulesReport }) {
  const s = rules.summary;
  return (
    <div className="rules">
      <div className="gate-summary">
        <div className={`gate-verdict ${s.hard_ok ? "ok" : "bad"}`}>{s.hard_ok ? "HARD OK" : "BLOCKED"}</div>
        <div className="metric">
          <span className="metric-value">{s.hard_passed}/{s.hard_total}</span>
          <span className="muted small">
            hard rules passed (100% required)
            {s.hard_waiting > 0 && ` · ${s.hard_waiting} waiting for approval`}
          </span>
        </div>
        <div className="metric">
          <span className={`metric-value ${s.soft_ok ? "" : "warn-text"}`}>{pct(s.soft_score)}</span>
          <span className="muted small">
            soft rules {s.soft_passed}/{s.soft_total} (target {pct(s.soft_target)}{s.soft_ok ? ", met" : ", not met"})
          </span>
        </div>
      </div>
      <p className="muted small">
        Every prompt passes these rules in code before any AI model sees it. A failed hard rule stops the request;
        soft rules never block, they only suggest how to ask better.
      </p>
      <h3 className="section-title">Hard rules</h3>
      <RuleTable rows={rules.hard} />
      <h3 className="section-title">Soft rules</h3>
      <RuleTable rows={rules.soft} soft />
    </div>
  );
}

export function rulesMark(rules: RulesReport): string {
  return rules.summary.hard_ok ? `soft ${pct(rules.summary.soft_score)}` : "blocked";
}
