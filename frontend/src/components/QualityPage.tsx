import { useState } from "react";
import { api } from "../api";
import type { Controls, GateResult } from "../types";

interface Props {
  controls: Controls;
  onControls: (c: Controls) => void;
}

const SIMS: { key: keyof Controls; label: string; help: string }[] = [
  { key: "chaos", label: "Chaos", help: "60% of model calls fail on purpose, to show retries and the fallback model." },
  { key: "outage", label: "Total outage", help: "Every model call fails. Known tasks use built-in templates; others open a ticket." },
  { key: "replay", label: "Replay", help: "Use recorded model responses instead of live calls." },
];

export default function QualityPage({ controls, onControls }: Props) {
  const [result, setResult] = useState<GateResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runGate() {
    setRunning(true);
    setError(null);
    try {
      setResult(await api.gate());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="quality">
      <section className="card">
        <h2 className="card-title">Simulation switches</h2>
        <p className="muted small">These apply to every new request you send from the Assistant page.</p>
        <div className="switches">
          {SIMS.map((s) => (
            <label key={s.key} className={`switch-row ${controls[s.key] ? "on" : ""}`}>
              <input type="checkbox" checked={controls[s.key]}
                     onChange={(e) => onControls({ ...controls, [s.key]: e.target.checked })} />
              <span className="switch" aria-hidden />
              <span>
                <strong>{s.label}</strong>
                <span className="muted small block">{s.help}</span>
              </span>
            </label>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="run-head">
          <div>
            <h2 className="card-title">Golden-set quality gate</h2>
            <p className="muted small">
              25 fixed cases. It passes when all hard cases pass and at least 85% of soft cases pass. CI runs the same gate on every push.
            </p>
          </div>
          <button className="primary" onClick={runGate} disabled={running}>{running ? "Running…" : "Run gate"}</button>
        </div>
        {error && <div className="callout bad compact">{error}</div>}
        {result && (
          <>
            <div className="gate-summary">
              <div className={`gate-verdict ${result.passed ? "ok" : "bad"}`}>{result.passed ? "PASS" : "FAIL"}</div>
              <div className="metric"><span className="metric-value">{result.hard.passed}/{result.hard.total}</span><span className="muted small">hard cases (all required)</span></div>
              <div className="metric"><span className="metric-value">{result.soft.passed}/{result.soft.total}</span><span className="muted small">soft cases (need {result.soft.needed})</span></div>
              {result.rules && (
                <>
                  <div className="metric"><span className="metric-value">{result.rules.hard.passed}/{result.rules.hard.total}</span><span className="muted small">hard rule cases (all required)</span></div>
                  <div className="metric"><span className="metric-value">{result.rules.soft.passed}/{result.rules.soft.total}</span><span className="muted small">soft rule cases (need {result.rules.soft.needed})</span></div>
                </>
              )}
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Case</th><th>Type</th><th>Area</th><th>Result</th><th>Detail</th></tr>
                </thead>
                <tbody>
                  {[...result.cases, ...(result.rules?.cases ?? [])].map((c) => (
                    <tr key={c.id}>
                      <td><strong>{c.id}</strong></td>
                      <td>{c.tier}</td>
                      <td>{c.area}</td>
                      <td><span className={`badge ${c.passed ? "ok" : "bad"}`}>{c.passed ? "Pass" : "Fail"}</span></td>
                      <td className="detail">{c.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
