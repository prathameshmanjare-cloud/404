import { useEffect, useState } from "react";
import { api } from "./api";
import type { Controls, Health, HistoryEntry, Reply, User } from "./types";
import Header, { type Page } from "./components/Header";
import Composer from "./components/Composer";
import HistoryList from "./components/HistoryList";
import RequestView from "./components/RequestView";
import QualityPage from "./components/QualityPage";

export default function App() {
  const [page, setPage] = useState<Page>("assistant");
  const [health, setHealth] = useState<Health | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserId] = useState("user_1");
  const [controls, setControls] = useState<Controls>({ chaos: false, outage: false, replay: false });
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .health()
      .then((h) => {
        setHealth(h);
        return api.users();
      })
      .then((u) => {
        setUsers(u);
        if (u.length && !u.some((x) => x.user_id === "user_1")) setUserId(u[0].user_id);
      })
      .catch(() => setBackendDown(true));
  }, []);

  const role = users.find((u) => u.user_id === userId)?.role;
  const current = history.find((h) => h.reply.thread_id === selected) ?? null;

  function upsert(reply: Reply) {
    setHistory((h) => {
      const i = h.findIndex((e) => e.reply.thread_id === reply.thread_id);
      if (i === -1) return [{ userId, at: Date.now(), reply }, ...h];
      const copy = [...h];
      copy[i] = { ...copy[i], reply };
      return copy;
    });
    setSelected(reply.thread_id);
  }

  async function send(text: string) {
    setBusy(true);
    setError(null);
    try {
      upsert(await api.submit(userId, text, controls));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      upsert(await api.resume(current.reply.thread_id, userId, decision));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (backendDown) {
    return (
      <div className="shell">
        <Header page={page} onPage={setPage} users={[]} userId={userId} onUser={setUserId} health={null}
                controls={controls} offline />
        <main className="center-state">
          <h2>The backend is not running</h2>
          <p className="muted">
            Start it in <code>backend/</code> with <code>uvicorn main:app --port 8000</code>, then reload this page.
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="shell">
      <Header page={page} onPage={setPage} users={users} userId={userId} onUser={setUserId} health={health}
              controls={controls} busy={busy} />
      {health && !health.key_loaded && health.llm_mode !== "replay" && (
        <div className="banner warn">
          <strong>No Groq API key loaded.</strong> Answers come only from the built-in templates (common tasks
          such as palindrome, prime or gcd); anything else cannot be generated. Add <code>GROQ_API_KEY</code> to{" "}
          <code>backend/.env</code> and restart the backend.
        </div>
      )}
      {error && (
        <div className="banner error" role="alert">
          <span>{error}</span>
          <button className="link" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      {page === "assistant" ? (
        <div className="workspace">
          <aside className="sidebar">
            <Composer busy={busy} onSend={send} />
            <HistoryList history={history} selected={selected} onSelect={setSelected} />
          </aside>
          <main className="main">
            {current ? (
              <RequestView key={current.reply.thread_id} entry={current} userId={userId} role={role} busy={busy}
                           onDecide={decide} />
            ) : (
              <div className="center-state">
                <h2>Ask for a Python function or a SQL query</h2>
                <p className="muted">
                  The assistant writes the code and its tests, checks them against the safety policy, runs the tests
                  in a sandbox, and lets you run the code with your own input.
                </p>
                <ol className="how">
                  <li><strong>Describe</strong> what you need in “New request”.</li>
                  <li><strong>Review</strong> the code, tests and safety decision in the tabs.</li>
                  <li><strong>Run</strong> it with your own values in the Run tab.</li>
                </ol>
              </div>
            )}
          </main>
        </div>
      ) : (
        <QualityPage controls={controls} onControls={setControls} />
      )}
    </div>
  );
}
