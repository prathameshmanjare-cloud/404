import type { HistoryEntry } from "../types";
import { statusInfo } from "./status";

interface Props {
  history: HistoryEntry[];
  selected: string | null;
  onSelect: (threadId: string) => void;
}

export default function HistoryList({ history, selected, onSelect }: Props) {
  return (
    <section className="card history" aria-label="Request history">
      <h2 className="card-title">
        Your history <span className="count">{`${history.length} / 3`}</span>
      </h2>
      <p className="muted small history-note">
        Your last 3 requests. Older ones are deleted automatically, and only you can see these.
      </p>
      {history.length === 0 ? (
        <p className="muted small">Your requests will appear here.</p>
      ) : (
        <ul>
          {history.map((h) => {
            const s = statusInfo(h.reply.status);
            return (
              <li key={h.reply.thread_id}>
                <button
                  className={`history-item ${selected === h.reply.thread_id ? "selected" : ""}`}
                  onClick={() => onSelect(h.reply.thread_id)}
                >
                  <span className="history-text">{h.reply.request ?? "(request)"}</span>
                  <span className="history-meta">
                    <span className={`badge ${s.tone}`}>{s.label}</span>
                    <span className="muted small">
                      {new Date(h.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
