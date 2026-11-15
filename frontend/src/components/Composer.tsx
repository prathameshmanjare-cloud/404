import { useState } from "react";

const EXAMPLES = [
  "Write a function to check if a number is a palindrome",
  "Write a JavaScript function that removes duplicates from an array",
  "My PAN is ABCPE1234F. Is it correct?",
  "Explain the difference between a list and a tuple",
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
        placeholder="Ask a question, or describe the Python or JavaScript code you need…"
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
