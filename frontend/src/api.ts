// One typed fetch wrapper per endpoint. Components never call fetch directly.
// Only VITE_BACKEND_URL is read here; no key ever goes in a frontend file.
import type {
  ApprovalQueue, Controls, ExecuteResult, GateResult, Health, Reply, RunRecord, SnippetApproval, TicketList,
  TicketRecord, User,
} from "./types";

const BASE = (import.meta.env.VITE_BACKEND_URL as string | undefined) ?? "http://localhost:8000";

// The session token from POST /session. The server decides who the caller is from this token alone;
// no request ever carries a user id. Kept in memory only (a reload signs in again).
let sessionToken: string | null = null;
let signedInUser: string | null = null;

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(sessionToken ? { "X-Session-Token": sessionToken } : {}),
      ...(init?.headers ?? {}),
    },
  });
  // The backend restarted (sessions are in memory): sign the same user in again once, then retry.
  if (res.status === 401 && signedInUser && !retried && path !== "/session") {
    await api.signIn(signedInUser);
    return request<T>(path, init, true);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  users: () => request<User[]>("/users"),
  // Sign in as a user from config.yaml (a demo stand-in for real login); later calls use the session only.
  signIn: async (user_id: string) => {
    const out = await request<{ token: string; user: User }>("/session", {
      method: "POST",
      body: JSON.stringify({ user_id }),
    });
    sessionToken = out.token;
    signedInUser = out.user.user_id;
    return out.user;
  },
  submit: (text: string, controls: Controls) =>
    request<Reply>("/submit", { method: "POST", body: JSON.stringify({ text, controls }) }),
  resume: (thread_id: string, decision: "approve" | "reject") =>
    request<Reply>("/resume", { method: "POST", body: JSON.stringify({ thread_id, decision }) }),
  execute: (thread_id: string, snippet: string) =>
    request<ExecuteResult>("/execute", { method: "POST", body: JSON.stringify({ thread_id, snippet }) }),
  // A snippet that reads files (or uses the network) waits for an instructor; instructors approve (and run) or reject it.
  snippetApprovals: (thread_id: string) =>
    request<SnippetApproval[]>(`/execute/approvals/${encodeURIComponent(thread_id)}`),
  decideSnippet: (snippet_id: string, decision: "approve" | "reject") =>
    request<ExecuteResult>("/execute/decide", { method: "POST", body: JSON.stringify({ snippet_id, decision }) }),
  // Instructors only: code and Run-tab inputs waiting for a decision (never students' history).
  approvals: () => request<ApprovalQueue>("/approvals"),
  // History: the server returns only the signed-in caller's own runs, whatever their role.
  runs: () => request<RunRecord[]>("/runs"),
  gate: () => request<GateResult>("/gate", { method: "POST" }),
  // Instructors only: the server refuses everyone else.
  tickets: (status: "all" | "open" | "closed" = "all") => request<TicketList>(`/tickets?status=${status}`),
  setTicketStatus: (ticket_id: string, status: "open" | "closed") =>
    request<TicketRecord>(`/tickets/${encodeURIComponent(ticket_id)}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  feedback: (thread_id: string, text: string) =>
    request<{ saved: boolean }>("/feedback", { method: "POST", body: JSON.stringify({ thread_id, text }) }),
};
