import React, { useState } from "react";

const TOOL_ICON = {
  search_knowledge: "🔎",
  list_files: "📂",
  read_file: "📄",
  query_dataframe: "📊",
  python_sandbox: "🐍",
};

function fmtArgs(args) {
  if (!args || typeof args !== "object") return null;
  // Compact summary for header — full args available in expanded view.
  const parts = Object.entries(args).slice(0, 3).map(([k, v]) => {
    if (v == null) return null;
    let s;
    if (typeof v === "string") {
      s = v.length > 64 ? `${v.slice(0, 64)}…` : v;
    } else if (Array.isArray(v)) {
      s = `[${v.length}]`;
    } else {
      s = String(v);
    }
    return `${k}=${s}`;
  });
  return parts.filter(Boolean).join(" · ");
}

function fmtMs(ms) {
  if (!ms || ms < 0) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function StepCard({ step, idx }) {
  const [open, setOpen] = useState(false);
  const icon = TOOL_ICON[step.name] || "🛠";
  const title = step.name;
  const summary = fmtArgs(step.args);
  return (
    <div className={`think-step${open ? " open" : ""}`}>
      <button className="think-step-head" onClick={() => setOpen((o) => !o)}>
        <span className="think-step-icon">{icon}</span>
        <span className="think-step-num">#{idx + 1}</span>
        <span className="think-step-title">{title}</span>
        {summary && <span className="think-step-summary">{summary}</span>}
        <span className="think-step-ms">{fmtMs(step.ms)}</span>
        <span className={`think-chevron ${open ? "open" : ""}`}>▸</span>
      </button>
      {open && (
        <div className="think-step-body">
          {step.args && Object.keys(step.args).length > 0 && (
            <>
              <div className="think-step-label">Arguments</div>
              <pre className="think-step-pre">
                {JSON.stringify(step.args, null, 2)}
              </pre>
            </>
          )}
          {step.result_preview && (
            <>
              <div className="think-step-label">Result</div>
              <pre className="think-step-pre">{step.result_preview}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Collapsible "Thinking" timeline rendered ABOVE a bot reply. Shows each
 * tool the agent invoked in chronological order, with timing and a
 * click-to-expand body for full args/results.
 *
 * Hidden when there were no tool calls (cheap greeting / known-fact answer).
 */
export default function ThinkingTimeline({ steps = [], totalMs = 0 }) {
  const [open, setOpen] = useState(false);
  if (!steps || steps.length === 0) return null;
  return (
    <div className={`thinking${open ? " thinking-open" : ""}`}>
      <button className="thinking-head" onClick={() => setOpen((o) => !o)}>
        <span className="thinking-brain">🧠</span>
        <span className="thinking-label">Thinking</span>
        <span className="thinking-meta">
          {steps.length} step{steps.length === 1 ? "" : "s"}
          {totalMs > 0 && ` · ${fmtMs(totalMs)}`}
        </span>
        <span className={`think-chevron ${open ? "open" : ""}`}>▸</span>
      </button>
      {open && (
        <div className="thinking-body">
          {steps.map((s, i) => (
            <StepCard key={i} step={s} idx={i} />
          ))}
        </div>
      )}
    </div>
  );
}
