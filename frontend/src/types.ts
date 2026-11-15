// Mirrors the HTTP contract in backend/main.py. Change both sides together.

export type Status =
  | "done"
  | "awaiting_approval"
  | "escalated"
  | "denied"
  | "rejected"
  | "refused"
  | "unavailable"
  | "invalid_output"
  | "sql";

export type Tier = "primary" | "fallback" | "template" | "none";

export interface User {
  user_id: string;
  name: string;
  role: "user" | "admin";
}

export interface Controls {
  chaos: boolean;
  outage: boolean;
  replay: boolean;
}

export interface Policy {
  decision: "allow" | "needs_approval" | "deny";
  reasons: string[];
}

export interface Ticket {
  ticket_id: string;
  user_id: string;
  priority: "P1" | "P2" | "P3";
  sla_hours: number;
  reason: string;
  handoff_note: string;
  request: string;
}

export interface RunInfo {
  exit_code: number | null;
  timed_out: boolean;
  output: string;
  sandbox: string;
  cached?: boolean;
}

export interface Reply {
  thread_id: string;
  status: Status;
  request: string | null; // masked by the backend
  route: string | null;
  code: string | null;
  tests: string | null;
  sql: string | null;
  report: string | null;
  tier: Tier;
  policy: Policy | null;
  ticket: Ticket | null;
  run: RunInfo | null;
  requirements?: { function_name?: string; parameters?: { name: string; type: string }[] } | null;
  example?: string;
}

export interface HistoryEntry {
  userId: string;
  at: number;
  reply: Reply;
}

export interface ExecuteResult {
  thread_id: string;
  exit_code: number | null;
  timed_out: boolean;
  output: string;
  sandbox: string;
}

export interface Health {
  status: string;
  llm_mode: string;
  key_loaded: boolean;
  models: Record<string, string>;
  sandbox: string;
}

export interface GateCase {
  id: string;
  tier: "hard" | "soft";
  area: string;
  passed: boolean;
  detail: string;
}

export interface GateResult {
  passed: boolean;
  hard: { passed: number; total: number };
  soft: { passed: number; total: number; needed: number };
  failing: string[];
  cases: GateCase[];
}
