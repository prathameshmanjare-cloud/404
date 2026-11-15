// One typed fetch wrapper per endpoint. Components never call fetch directly.
// Only VITE_BACKEND_URL is read here; no key ever goes in a frontend file.
import type { Controls, ExecuteResult, GateResult, Health, Reply, User } from "./types";

const BASE = (import.meta.env.VITE_BACKEND_URL as string | undefined) ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
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
  submit: (user_id: string, text: string, controls: Controls) =>
    request<Reply>("/submit", { method: "POST", body: JSON.stringify({ user_id, text, controls }) }),
  resume: (thread_id: string, user_id: string, decision: "approve" | "reject") =>
    request<Reply>("/resume", { method: "POST", body: JSON.stringify({ thread_id, user_id, decision }) }),
  execute: (thread_id: string, user_id: string, snippet: string) =>
    request<ExecuteResult>("/execute", { method: "POST", body: JSON.stringify({ thread_id, user_id, snippet }) }),
  gate: () => request<GateResult>("/gate", { method: "POST" }),
  feedback: (user_id: string, thread_id: string, text: string) =>
    request<{ saved: boolean }>("/feedback", {
      method: "POST",
      body: JSON.stringify({ user_id, thread_id, text }),
    }),
};
