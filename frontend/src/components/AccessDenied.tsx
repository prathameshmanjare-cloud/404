import type { Denial } from "../types";

const KIND_LABEL: Record<Denial["kind"], string> = {
  impersonation: "Pretending to be another user",
  role: "Claiming a role you don't have",
  user_data: "Asking for another user's data",
  group_data: "Asking for other users' data",
  quota: "Usage limit",
};

export default function AccessDenied({ denial }: { denial: Denial }) {
  return (
    <section className="denied" role="alert" aria-labelledby="denied-title">
      <div className="denied-icon" aria-hidden>
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round">
          <rect x="4" y="10" width="16" height="11" rx="2" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3" />
        </svg>
      </div>
      <div className="denied-body">
        <div className="denied-kind">{KIND_LABEL[denial.kind]}</div>
        <h2 id="denied-title">{denial.title}</h2>
        <p>{denial.reason}</p>
        <ul className="denied-points">
          <li>{denial.allowed}</li>
          <li>{denial.assurance}</li>
        </ul>
      </div>
    </section>
  );
}
