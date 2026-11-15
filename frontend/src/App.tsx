import { useEffect, useState } from "react";
import { api } from "./api";
import type {
  ApprovalQueue as Queue, Controls, ExecuteResult, Health, HistoryEntry, QueuedSnippet, Reply, RunRecord, User,
} from "./types";
import Header, { type Page } from "./components/Header";
import Composer from "./components/Composer";
import HistoryList from "./components/HistoryList";
import ApprovalQueue from "./components/ApprovalQueue";
import RequestView from "./components/RequestView";
import QualityPage from "./components/QualityPage";
import TicketsPage from "./components/TicketsPage";

function toEntries(runs: RunRecord[]): HistoryEntry[] {
  return runs.map((r) => ({ userId: r.owner, at: r.created * 1000, reply: r.reply }));
}

export default function App() {
  const [page, setPage] = useState<Page>("assistant");
  const [health, setHealth] = useState<Health | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserId] = useState("student_a");
  const [controls, setControls] = useState<Controls>({ chaos: false, outage: false, replay: false });
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [openTickets, setOpenTickets] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // Instructors only: what waits for a decision, the item being reviewed (not part of any history), and
  // the outcome of snippets decided in this session.
  const [queue, setQueue] = useState<Queue | null>(null);
  const [review, setReview] = useState<HistoryEntry | null>(null);
  const [snippetResults, setSnippetResults] = useState<Record<string, ExecuteResult>>({});

  useEffect(() => {
    api
      .health()
      .then((h) => {
        setHealth(h);
        return api.users();
      })
      .then((u) => {
        setUsers(u);
        if (u.length && !u.some((x) => x.user_id === "student_a")) setUserId(u[0].user_id);
      })
      .catch(() => setBackendDown(true));
  }, []);

  const role = users.find((u) => u.user_id === userId)?.role;

  // Whenever the signed-in user changes, drop everything on screen and load THAT user's history from the
  // backend, which only returns the caller's own runs. Nothing from the previous user stays visible.
  useEffect(() => {
    let cancelled = false;
    setHistory([]);
    setSelected(null);
    setQueue(null);
    setReview(null);
    setSnippetResults({});
    setError(null);
    setSignedIn(false);
    api
      .signIn(userId)
      .then(() => api.runs())
      .then((runs) => {
        if (!cancelled) {
          setHistory(toEntries(runs));
          setSignedIn(true);
        }
        return Promise.all([refreshTicketCount(), refreshQueue()]);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  // Instructors see how many tickets are open; everyone else never asks (the server would refuse anyway).
  const isAdmin = role === "instructor";
  async function refreshTicketCount() {
    if (users.find((u) => u.user_id === userId)?.role !== "instructor") {
      setOpenTickets(0);
      return;
    }
    try {
      setOpenTickets((await api.tickets("open")).counts.open);
    } catch {
      /* not critical */
    }
  }
  useEffect(() => {
    if (!isAdmin && page === "tickets") setPage("assistant");   // the Tickets page is for instructors only
  }, [isAdmin, page]);

  async function refreshQueue() {
    if (users.find((u) => u.user_id === userId)?.role !== "instructor") return;
    try {
      setQueue(await api.approvals());
    } catch {
      /* keep what is on screen */
    }
  }

  function openQueued(runId: string) {
    const item = queue?.code.find((c) => c.run_id === runId);
    if (!item) return;
    setSelected(null);
    setReview({ userId: item.owner_name, at: item.created * 1000, reply: item.reply });
  }

  async function decideSnippet(s: QueuedSnippet, decision: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      const out = await api.decideSnippet(s.snippet_id, decision);
      setSnippetResults((r) => ({ ...r, [s.snippet_id]: out }));
      await refreshQueue();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // The backend keeps only each user's last 3 requests; reload so older ones drop off the list too.
  async function refreshHistory() {
    try {
      setHistory(toEntries(await api.runs()));
    } catch {
      /* keep what is on screen */
    }
  }
  const current = review ?? history.find((h) => h.reply.thread_id === selected) ?? null;

  function upsert(reply: Reply) {
    setHistory((h) => {
      const i = h.findIndex((e) => e.reply.thread_id === reply.thread_id);
      if (i === -1) return [{ userId, at: Date.now(), reply }, ...h];
      const copy = [...h];
      copy[i] = { ...copy[i], reply };
      return copy;
    });
    setSelected(reply.thread_id);
    setReview(null);
  }

  async function send(text: string) {
    setBusy(true);
    setPending(text);
    setError(null);
    try {
      const reply = await api.submit(text, controls);
      await refreshHistory();       // first: the server's list (3 most recent, older ones dropped)
      upsert(reply);                // then: this reply on top, so notes like "reused" stay visible
      await refreshTicketCount();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      const reply = await api.resume(current.reply.thread_id, decision);
      if (review) {
        // A student's request from the approval queue: show the outcome once, never add it to any history.
        setReview({ ...review, reply });
        await refreshQueue();
        return;
      }
      await refreshHistory();
      upsert(reply);
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
              controls={controls} busy={busy} isAdmin={isAdmin} openTickets={openTickets} />
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
            <Composer busy={busy || !signedIn} onSend={send} />
            {isAdmin && (
              <ApprovalQueue queue={queue} selected={review?.reply.thread_id ?? null} busy={busy}
                             onSelectCode={openQueued} onDecideSnippet={decideSnippet}
                             snippetResults={snippetResults} onRefresh={refreshQueue} />
            )}
            <HistoryList history={history} selected={review ? null : selected}
                         onSelect={(id) => { setReview(null); setSelected(id); }} />
          </aside>
          <main className="main">
            {pending ? (
              <section className="card pending" aria-live="polite">
                <div className="spinner" aria-hidden />
                <div>
                  <div className="eyebrow">Working on your request</div>
                  <p className="pending-text">Your request is being masked and processed…</p>
                  <p className="muted small">
                    Masking personal data, understanding the request, then writing and testing code in the sandbox
                    (or writing an answer). Code requests usually take 10–30 seconds.
                  </p>
                </div>
              </section>
            ) : current ? (
              <RequestView key={current.reply.thread_id} entry={current} userId={userId} role={role} busy={busy}
                           reviewing={!!review}
                           onDecide={decide} />
            ) : (
              <div className="center-state">
                <h2>Ask a question, or ask for Python, JavaScript or SQL</h2>
                <p className="muted">
                  For code, the assistant writes the code and its tests, checks them against the safety policy, runs
                  the tests in a sandbox, and lets you run the code with your own input. Personal data such as PAN,
                  Aadhaar or card numbers is masked before any model sees it, and checked in code.
                </p>
                <ol className="how">
                  <li><strong>Ask</strong> a question or describe the code you need in “New request”.</li>
                  <li><strong>Review</strong> the answer, or the code, tests and safety decision in the tabs.</li>
                  <li><strong>Run</strong> code with your own values in the Run tab.</li>
                </ol>
              </div>
            )}
          </main>
        </div>
      ) : page === "tickets" && isAdmin ? (
        <TicketsPage onCounts={setOpenTickets} />
      ) : (
        <QualityPage controls={controls} onControls={setControls} />
      )}
    </div>
  );
}
