import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ExecuteResult, Reply, SnippetApproval } from "../types";

interface Props {
  reply: Reply;
  userId: string;
  role?: string;
}

function blockedReason(reply: Reply): string | null {
  switch (reply.status) {
    case "denied":
      return "The safety check blocked this code, so it cannot be run.";
    case "awaiting_approval":
      return "This code needs an instructor's approval before it can run.";
    case "rejected":
      return "An instructor rejected this code, so it cannot be run.";
    default:
      return null;
  }
}

export default function RunPanel({ reply, userId, role }: Props) {
  const js = reply.language === "javascript";
  const initial = reply.example || (js ? "// Call the exported function here, for example:\n// myFunction(10)"
                                       : "# Call the generated function here, for example:\n# print(my_function(10))");
  const [snippet, setSnippet] = useState(initial);
  const [result, setResult] = useState<ExecuteResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Snippets of this thread that read files (or use the network) wait here for an instructor's decision.
  const [approvals, setApprovals] = useState<SnippetApproval[]>([]);
  const blocked = blockedReason(reply);
  const isAdmin = role === "instructor";

  const loadApprovals = useCallback(async () => {
    try {
      setApprovals(await api.snippetApprovals(reply.thread_id));
    } catch {
      /* not critical: the list is only a convenience */
    }
  }, [reply.thread_id]);
  useEffect(() => {
    if (!blocked) loadApprovals();
  }, [blocked, loadApprovals, userId]);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setResult(await api.execute(reply.thread_id, snippet));
      await loadApprovals();
    } catch (e) {
      setResult(null);
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  async function decide(a: SnippetApproval, decision: "approve" | "reject") {
    setRunning(true);
    setError(null);
    try {
      setResult(await api.decideSnippet(a.snippet_id, decision));
      await loadApprovals();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  if (blocked) return <p className="muted">{blocked}</p>;

  // A backend from before snippet approvals sends no status: its reply is always a finished run.
  const ran = result && (result.status ?? "ran") === "ran" ? result : null;
  const ok = ran && !ran.timed_out && ran.exit_code === 0;
  const pending = approvals.filter((a) => a.status === "pending");
  return (
    <div className="run">
      <div className="run-input">
        <div className="run-head">
          <h3 className="section-title">Your input</h3>
          <button className="ghost small" onClick={() => setSnippet(initial)} disabled={running}>Reset example</button>
        </div>
        <p className="muted small">
          {js
            ? "Write JavaScript that calls the exported functions. The value of the last line is printed, as in the Node.js console."
            : "Write Python that calls the generated code. The value of the last line is printed, as in a Python shell."}{" "}
          It runs in the sandbox: no network, no secrets and a time limit. OS access is refused; reading or
          opening a file waits for an instructor's approval.
        </p>
        <textarea
          className="mono"
          value={snippet}
          onChange={(e) => setSnippet(e.target.value)}
          rows={6}
          spellCheck={false}
          aria-label="Code to run"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              if (!running) run();
            }
          }}
        />
        <div className="composer-actions">
          <span className="muted small">Ctrl/⌘ + Enter to run</span>
          <button className="primary" onClick={run} disabled={running}>
            {running ? "Running…" : "▶ Run"}
          </button>
        </div>
      </div>

      <div className="run-output">
        <div className="run-head">
          <h3 className="section-title">Output</h3>
          {ran && (
            <span className={`badge ${ok ? "ok" : "bad"}`}>
              {ran.timed_out ? "Stopped: time limit" : ok ? "Success" : `Error (exit ${ran.exit_code})`}
            </span>
          )}
          {result?.status === "needs_approval" && <span className="badge warn">Waiting for approval</span>}
          {result?.status === "rejected" && <span className="badge bad">Rejected</span>}
        </div>
        {error && <div className="callout bad compact">{error}</div>}
        {result?.status === "needs_approval" && (
          <div className="callout warn compact">
            <div>
              <h3>Approval needed before this input can run</h3>
              <p>It reads or opens a file (or uses the network), so an instructor decides first. Nothing has run.</p>
              <ul>{(result.policy?.reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul>
              {!isAdmin && (
                <p className="muted small">Ask an instructor to approve it, then press Run again with the same input.</p>
              )}
            </div>
          </div>
        )}
        {result?.status === "rejected" && (
          <div className="callout bad compact">An instructor rejected this input, so it was not run.</div>
        )}
        {!error && !result && <div className="console empty">Press Run to see the output here.</div>}
        {/* Program output is untrusted: plain text only. */}
        {ran && <pre className={`console ${ok ? "" : "has-error"}`}>{ran.output || "(no output)"}</pre>}

        {pending.length > 0 && (
          <div className="snippet-approvals">
            <h3 className="section-title">Waiting for approval ({pending.length})</h3>
            {pending.map((a) => (
              <div key={a.snippet_id} className="callout warn compact">
                <div>
                  <p className="muted small">
                    Requested by {a.requested_by} at{" "}
                    {new Date(a.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </p>
                  {/* Masked on the server; shown as plain text. */}
                  <pre className="console">{a.snippet}</pre>
                  <ul>{a.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
                  {!isAdmin && <p className="muted small">Only an instructor can decide. Switch “Signed in as” to Instructor.</p>}
                </div>
                {isAdmin && (
                  <div className="callout-actions">
                    <button className="primary" disabled={running} onClick={() => decide(a, "approve")}>
                      Approve and run
                    </button>
                    <button className="secondary" disabled={running} onClick={() => decide(a, "reject")}>
                      Reject
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
