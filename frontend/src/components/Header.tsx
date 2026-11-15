import type { Controls, Health, User } from "../types";
import Logo from "./Logo";

export type Page = "assistant" | "quality" | "tickets";

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
  isAdmin?: boolean;
  openTickets?: number;
}

function connection(health: Health | null, offline?: boolean): { label: string; tone: string } {
  if (offline) return { label: "Backend offline", tone: "bad" };
  if (!health) return { label: "Connecting…", tone: "muted" };
  if (health.llm_mode === "replay") return { label: "Replay mode", tone: "info" };
  if (!health.key_loaded) return { label: "No API key · templates only", tone: "warn" };
  return { label: "Connected · live models", tone: "ok" };
}

export default function Header({ page, onPage, users, userId, onUser, health, controls, busy, offline, isAdmin,
                                 openTickets }: Props) {
  const conn = connection(health, offline);
  const sims = [controls.chaos && "Chaos 60%", controls.outage && "Outage", controls.replay && "Replay"].filter(Boolean);
  return (
    <header className="header">
      <div className="brand">
        <Logo size={38} />
        <div>
          <div className="brand-name">
            CodeSmith <span className="brand-by">by 404</span>
          </div>
          <div className="brand-sub">Python &amp; JavaScript assistant with privacy and safety checks</div>
        </div>
      </div>
      <nav className="nav" aria-label="Pages">
        <button className={page === "assistant" ? "active" : ""} onClick={() => onPage("assistant")}>
          Assistant
        </button>
        <button className={page === "quality" ? "active" : ""} onClick={() => onPage("quality")}>
          Quality &amp; reliability
        </button>
        {isAdmin && (
          <button className={page === "tickets" ? "active" : ""} onClick={() => onPage("tickets")}>
            Tickets{openTickets ? <span className="nav-count" title="Open tickets">{openTickets}</span> : null}
          </button>
        )}
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
