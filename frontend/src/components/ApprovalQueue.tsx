import type { ApprovalQueue as Queue, ExecuteResult, QueuedSnippet } from "../types";

interface Props {
  queue: Queue | null;
  selected: string | null;
  busy: boolean;
  onSelectCode: (runId: string) => void;
  onDecideSnippet: (s: QueuedSnippet, decision: "approve" | "reject") => void;
  snippetResults: Record<string, ExecuteResult>;
  onRefresh: () => void;
}

const time = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// The instructor's only view of students' work: what is waiting for a decision right now. Once decided, an
// item leaves the queue; students' history, finished runs and answers are never shown here.
export default function ApprovalQueue({
  queue, selected, busy, onSelectCode, onDecideSnippet, snippetResults, onRefresh,
}: Props) {
  const code = queue?.code ?? [];
  const snippets = queue?.snippets ?? [];
  const decided = Object.entries(snippetResults);
  return (
    <section className="card history" aria-label="Approval queue">
      <h2 className="card-title">
        Waiting for approval <span className="count">{code.length + snippets.length}</span>
        <button className="ghost small queue-refresh" onClick={onRefresh} disabled={busy}>Refresh</button>
      </h2>
      <p className="muted small history-note">
        Only requests that need your decision appear here. Students' history stays private to them.
      </p>
      {code.length + snippets.length === 0 && decided.length === 0 && (
        <p className="muted small">Nothing is waiting for approval.</p>
      )}
      {code.length > 0 && (
        <ul>
          {code.map((c) => (
            <li key={c.run_id}>
              <button className={`history-item ${selected === c.run_id ? "selected" : ""}`}
                      onClick={() => onSelectCode(c.run_id)}>
                <span className="history-text">{c.reply.request ?? "(request)"}</span>
                <span className="history-meta">
                  <span className="badge warn">Code</span>
                  <span className="muted small">{c.owner_name} · {time(c.created)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {snippets.map((s) => (
        <div key={s.snippet_id} className="callout warn compact queue-snippet">
          <div>
            <p className="muted small">
              <span className="badge warn">Run input</span> {s.owner_name} · {time(s.created)}
            </p>
            <p className="small">For: {s.request}</p>
            {/* Masked on the server; shown as plain text. */}
            <pre className="console">{s.snippet}</pre>
            <ul>{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </div>
          <div className="callout-actions">
            <button className="primary" disabled={busy} onClick={() => onDecideSnippet(s, "approve")}>
              Approve and run
            </button>
            <button className="secondary" disabled={busy} onClick={() => onDecideSnippet(s, "reject")}>
              Reject
            </button>
          </div>
        </div>
      ))}
      {decided.map(([id, r]) => (
        <div key={id} className={`callout ${r.status === "ran" ? "muted" : "bad"} compact`}>
          <div>
            <p className="small">
              {r.status === "ran" ? "Approved and run. The student can now run the same input." : "Rejected. Nothing ran."}
            </p>
            {r.status === "ran" && <pre className="console">{r.output || "(no output)"}</pre>}
          </div>
        </div>
      ))}
    </section>
  );
}
