import type { Controls, Health, User } from "../types";

export type Page = "assistant" | "quality";

interface Props {
  page: Page;
  onPage: (p: Page) => void;
  users: User[];
  userId: string;
  onUser: (id: string) => void;
  health: Health | null;
  controls: Controls;
  busy?: boolean;
  offline?: boolean;
}

function connection(health: Health | null, offline?: boolean): { label: string; tone: string } {
  if (offline) return { label: "Backend offline", tone: "bad" };
  if (!health) return { label: "Connecting…", tone: "muted" };
  if (health.llm_mode === "replay") return { label: "Replay mode", tone: "info" };
  if (!health.key_loaded) return { label: "No API key · templates only", tone: "warn" };
  return { label: "Connected · live models", tone: "ok" };
}

export default function Header({ page, onPage, users, userId, onUser, health, controls, busy, offline }: Props) {
  const conn = connection(health, offline);
  const sims = [controls.chaos && "Chaos 60%", controls.outage && "Outage", controls.replay && "Replay"].filter(Boolean);
  return (
    <header className="header">
      <div className="brand">
        <span className="logo" aria-hidden>{"</>"}</span>
        <div>
          <div className="brand-name">CodeForge</div>
          <div className="brand-sub">Code assistant with safety checks</div>
        </div>
      </div>
      <nav className="nav" aria-label="Pages">
        <button className={page === "assistant" ? "active" : ""} onClick={() => onPage("assistant")}>
          Assistant
        </button>
        <button className={page === "quality" ? "active" : ""} onClick={() => onPage("quality")}>
          Quality &amp; reliability
        </button>
      </nav>
      <div className="header-right">
        {sims.length > 0 && <span className="pill warn" title="Simulation switches are on">{sims.join(" · ")}</span>}
        <span className={`pill ${conn.tone}`}><span className="dot" />{conn.label}</span>
        <label className="user-switch">
          <span className="muted small">Signed in as</span>
          <select value={userId} onChange={(e) => onUser(e.target.value)} disabled={busy || users.length === 0}>
            {users.map((u) => (
              <option key={u.user_id} value={u.user_id}>
                {u.name} · {u.role}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}
