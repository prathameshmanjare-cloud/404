import { useState } from "react";
import { api } from "../api";
import type { ExecuteResult, Reply } from "../types";

interface Props {
  reply: Reply;
  userId: string;
}

function blockedReason(reply: Reply): string | null {
  switch (reply.status) {
    case "denied":
      return "The safety check blocked this code, so it cannot be run.";
    case "awaiting_approval":
      return "This code needs an admin's approval before it can run.";
    case "rejected":
      return "An admin rejected this code, so it cannot be run.";
    default:
      return null;
  }
}

export default function RunPanel({ reply, userId }: Props) {
  const js = reply.language === "javascript";
  const initial = reply.example || (js ? "// Call the exported function here, for example:\n// myFunction(10)"
                                       : "# Call the generated function here, for example:\n# print(my_function(10))");
  const [snippet, setSnippet] = useState(initial);
  const [result, setResult] = useState<ExecuteResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = blockedReason(reply);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setResult(await api.execute(reply.thread_id, snippet));
    } catch (e) {
      setResult(null);
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  if (blocked) return <p className="muted">{blocked}</p>;

  const ok = result && !result.timed_out && result.exit_code === 0;
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
          It runs in the sandbox: no network, no secrets, a time limit, and file or OS access is refused.
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
          {result && (
            <span className={`badge ${ok ? "ok" : "bad"}`}>
              {result.timed_out ? "Stopped: time limit" : ok ? "Success" : `Error (exit ${result.exit_code})`}
            </span>
          )}
        </div>
        {error && <div className="callout bad compact">{error}</div>}
        {!error && !result && <div className="console empty">Press Run to see the output here.</div>}
        {/* Program output is untrusted: plain text only. */}
        {result && <pre className={`console ${ok ? "" : "has-error"}`}>{result.output || "(no output)"}</pre>}
      </div>
    </div>
  );
}
