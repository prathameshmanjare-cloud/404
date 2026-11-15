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
  | "answered"
  | "access_denied"
  | "quota_exceeded"
  | "sql";

export type Tier = "primary" | "fallback" | "template" | "none";

export interface User {
  user_id: string;
  name: string;
  role: "user" | "instructor";
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

// Why a request was refused for trying to reach another user's data or identity.
export interface Denial {
  kind: "impersonation" | "role" | "user_data" | "group_data" | "quota";
  title: string;
  reason: string;
  allowed: string;
  assurance: string;
}

// A sensitive value found in the request: its type and the result of the code checks. Never the value.
export interface PiiFinding {
  type: string;
  placeholder: string;
  valid: boolean | null;
  detail: string;
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

// The central rules (backend rules.py) a response went through: hard = required, soft = advice (target 85%).
export interface RuleResult {
  id: string;
  name: string;
  tier: "hard" | "soft";
  stage: "prompt" | "code";
  status: "pass" | "fail" | "approval" | "n/a";
  detail: string;
  tip: string;
}

export interface RulesReport {
  hard: RuleResult[];
  soft: RuleResult[];
  summary: {
    hard_passed: number;
    hard_total: number;
    hard_failed: number;
    hard_waiting: number;
    hard_ok: boolean;
    soft_passed: number;
    soft_total: number;
    soft_score: number;
    soft_target: number;
    soft_ok: boolean;
    soft_needed: number;
  };
}

export interface Reply {
  thread_id: string;
  status: Status;
  request: string | null; // masked by the backend
  route: string | null;
  language: "python" | "javascript" | null;
  answer: string | null;
  pii: PiiFinding[];
  rules?: RulesReport | null;
  denial?: Denial | null;
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
  advice?: string[];                       // what to ask instead, when denied
  blocked_before_generation?: boolean;     // refused from the request alone: no code was written
  reused?: { run_id: string; at: number; request_key: string } | null;   // same request as before
  run_key?: string | null;                 // hash of user + code + tests (first 12 characters)
}

// Instructors only: what waits for a decision. Students' finished runs are never part of it.
export interface QueuedCode {
  run_id: string;
  owner: string;
  owner_name: string;
  created: number;
  reply: Reply;
}

export interface QueuedSnippet extends SnippetApproval {
  owner_name: string;
  request: string;
  language: string | null;
}

export interface ApprovalQueue {
  code: QueuedCode[];
  snippets: QueuedSnippet[];
}

export interface RunRecord {
  run_id: string;
  owner: string;
  created: number; // seconds since epoch
  reply: Reply;
}

export interface HistoryEntry {
  userId: string;
  at: number;
  reply: Reply;
}

// A Run-tab snippet that needs a person (file or network access). The snippet text is masked.
export interface SnippetApproval {
  snippet_id: string;
  thread_id: string;
  snippet: string;
  requested_by: string;
  reasons: string[];
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  created: number;
}

export interface ExecuteResult {
  thread_id: string;
  // ran: output below; needs_approval: waiting for an instructor; rejected: an instructor said no (nothing ran)
  status?: "ran" | "needs_approval" | "rejected";   // missing on older backends: a finished run
  exit_code: number | null;
  timed_out: boolean;
  output: string;
  sandbox: string;
  policy?: { decision: string; reasons: string[] };
  approval?: SnippetApproval | null;
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
  // The central rules suite: 20 hard + 20 soft cases (all hard required, 85% of soft).
  rules?: {
    passed: boolean;
    hard: { passed: number; total: number };
    soft: { passed: number; total: number; needed: number };
    cases: GateCase[];
  } | null;
  failing: string[];
  cases: GateCase[];
}

// A ticket as the instructor's Tickets page shows it (all text already masked by the backend).
export interface TicketRecord {
  ticket_id: string;
  run_id: string | null;
  user_id: string;
  priority: "P1" | "P2" | "P3";
  sla_hours: number;
  reason: string;
  handoff_note: string;
  request: string;
  status: "open" | "closed";
  created: number;       // seconds since epoch
  due: number;
  closed_at: number | null;
  closed_by: string | null;
  age_minutes: number;   // open: waiting so far; closed: time taken to close
  minutes_left: number | null;
  within_sla: boolean;
}

export interface TicketList {
  tickets: TicketRecord[];
  counts: { open: number; closed: number };
}
