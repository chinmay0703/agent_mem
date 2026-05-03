import React, { useState } from "react";

function fmtDateRange(t) {
  const parts = [];
  if (t.valid_from) parts.push(`from ${t.valid_from}`);
  if (t.valid_until) parts.push(`until ${t.valid_until}`);
  return parts.length ? ` [${parts.join(", ")}]` : "";
}

function TripleLine({ t, color }) {
  return (
    <div className="meta-triple">
      <span style={{ color }}>{t.subject}</span>
      <span className="meta-rel">{t.relation}</span>
      <span style={{ color }}>{t.object}</span>
      {(t.valid_from || t.valid_until) && (
        <span className="meta-dates">{fmtDateRange(t)}</span>
      )}
      {typeof t.confidence === "number" && t.confidence > 0 && (
        <span className="meta-conf">conf {t.confidence.toFixed(2)}</span>
      )}
    </div>
  );
}

function asArr(v) {
  return Array.isArray(v) ? v : [];
}

function srcLabel(s) {
  if (s?.kind === "file") {
    const name = s.filename || "file";
    const page = s.page ? ` p.${s.page}` : "";
    return `${name}${page}`;
  }
  if (s?.kind === "message") {
    const when = s.created_at ? new Date(s.created_at).toLocaleDateString() : "";
    const role = s.role === "user" ? "You" : "Assistant";
    return `${role} · chat${when ? " · " + when : ""}`;
  }
  return s?.src_id || "source";
}

export default function MessageMeta({
  added, updated, reinforced, removed, toolCalls, sources,
}) {
  const [open, setOpen] = useState(false);

  const a = asArr(added);
  const u = asArr(updated);
  const r = asArr(reinforced);
  const d = asArr(removed);
  const tc = asArr(toolCalls);
  const s = asArr(sources);

  const total = a.length + u.length + r.length + d.length + tc.length + s.length;
  if (total === 0) return null;

  return (
    <div className="meta">
      <button
        className="meta-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={`chevron ${open ? "open" : ""}`}>▸</span>
        {a.length > 0 && (
          <span className="meta-pill meta-pill-add">+{a.length} added</span>
        )}
        {u.length > 0 && (
          <span className="meta-pill meta-pill-upd">↻ {u.length} updated</span>
        )}
        {r.length > 0 && (
          <span className="meta-pill meta-pill-rein">
            · {r.length} reinforced
          </span>
        )}
        {d.length > 0 && (
          <span className="meta-pill meta-pill-del">−{d.length} removed</span>
        )}
        {tc.length > 0 && (
          <span className="meta-pill meta-pill-tool">
            🛠 {tc.length} tool{tc.length === 1 ? "" : "s"}
          </span>
        )}
        {s.length > 0 && (
          <span className="meta-pill meta-pill-src">
            📎 {s.length} source{s.length === 1 ? "" : "s"}
          </span>
        )}
        <span className="meta-hint">{open ? "hide details" : "details"}</span>
      </button>

      {open && (
        <div className="meta-body">
          {a.length > 0 && (
            <div className="meta-section">
              <div className="meta-section-title">
                <span className="meta-dot meta-dot-add" />
                Added to graph
              </div>
              {a.map((t, i) => (
                <TripleLine key={`a${i}`} t={t} color="#0e8a5e" />
              ))}
            </div>
          )}
          {u.length > 0 && (
            <div className="meta-section">
              <div className="meta-section-title">
                <span className="meta-dot meta-dot-upd" />
                Updated in graph
              </div>
              {u.map((t, i) => (
                <TripleLine key={`u${i}`} t={t} color="#b07a00" />
              ))}
            </div>
          )}
          {r.length > 0 && (
            <div className="meta-section">
              <div className="meta-section-title">
                <span className="meta-dot meta-dot-rein" />
                Reinforced (already known)
              </div>
              {r.map((t, i) => (
                <TripleLine key={`r${i}`} t={t} color="var(--text-1)" />
              ))}
            </div>
          )}
          {d.length > 0 && (
            <div className="meta-section">
              <div className="meta-section-title">
                <span className="meta-dot meta-dot-del" />
                Removed from graph
              </div>
              {d.map((t, i) => (
                <TripleLine key={`d${i}`} t={t} color="var(--danger)" />
              ))}
            </div>
          )}
          {s.length > 0 && (
            <div className="meta-section">
              <div className="meta-section-title">
                <span className="meta-dot meta-dot-src" />
                Sources cited
              </div>
              {s.map((src, i) => (
                <div className="meta-source" key={`s${i}`}>
                  <span className="cite-chip cite-chip-static">{i + 1}</span>
                  <div className="meta-source-body">
                    <div className="meta-source-label">{srcLabel(src)}</div>
                    {src.snippet && (
                      <div className="meta-source-snippet">{src.snippet}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          {tc.length > 0 && (
            <div className="meta-section">
              <div className="meta-section-title">
                <span className="meta-dot meta-dot-tool" />
                Tool calls
              </div>
              {tc.map((c, i) => (
                <div className="meta-tool" key={`tc${i}`}>
                  <div className="meta-tool-name">🛠 {c.name}</div>
                  {c.args && Object.keys(c.args).length > 0 && (
                    <pre className="meta-tool-args">
                      {JSON.stringify(c.args, null, 2)}
                    </pre>
                  )}
                  {c.result_preview && (
                    <pre className="meta-tool-result">{c.result_preview}</pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
