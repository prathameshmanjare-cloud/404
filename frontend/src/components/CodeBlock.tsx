import { useState } from "react";

interface Props {
  code: string;
  title?: string;
  lang?: string;
}

// Code is untrusted text: rendered inside <pre> as plain text, never as HTML.
export default function CodeBlock({ code, title, lang = "python" }: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  }

  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span className="muted small">{title ?? lang}</span>
        <button className="ghost small" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <pre>
        {lines.map((line, i) => (
          <div key={i} className="code-line">
            <span className="ln" aria-hidden>{i + 1}</span>
            <span>{line || " "}</span>
          </div>
        ))}
      </pre>
    </div>
  );
}
