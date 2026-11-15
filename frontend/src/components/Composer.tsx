import { useState } from "react";

const EXAMPLES = [
  "Write a function to check if a number is a palindrome",
  "Write a function that returns the n-th Fibonacci number",
  "Write a function that checks whether two words are anagrams",
  "Query total sales per region",
];

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
}

export default function Composer({ busy, onSend }: Props) {
  const [text, setText] = useState("");

  function submit() {
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  }

  return (
    <section className="card composer" aria-label="New request">
      <h2 className="card-title">New request</h2>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Describe the function or query you need…"
        rows={4}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-actions">
        <span className="muted small">Ctrl/⌘ + Enter to send</span>
        <button className="primary" onClick={submit} disabled={busy || !text.trim()}>
          {busy ? "Working…" : "Send"}
        </button>
      </div>
      <div className="examples">
        <span className="muted small">Try:</span>
        {EXAMPLES.map((ex) => (
          <button key={ex} className="chip" onClick={() => setText(ex)} disabled={busy}>
            {ex}
          </button>
        ))}
      </div>
    </section>
  );
}
