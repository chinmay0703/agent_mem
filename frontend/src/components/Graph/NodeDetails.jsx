import React, { useMemo, useState } from "react";
import { deleteTriple } from "../../api/client.js";
import { useConfirm } from "../Confirm.jsx";
import { useToast } from "../Toast.jsx";

const TYPE_COLORS = {
  user: "#3b6ee8",
  company: "#16a875",
  goal: "#e6862b",
  preference: "#8a4ee0",
  person: "#e0517f",
  topic: "#1ea7c1",
  other: "#6f7c93",
};

function fmtDateRange(e) {
  const parts = [];
  if (e.valid_from) parts.push(`from ${e.valid_from}`);
  if (e.valid_until) parts.push(`until ${e.valid_until}`);
  return parts.length ? ` [${parts.join(", ")}]` : "";
}

/**
 * Side panel that opens when the user clicks a node in the graph.
 * Lists every (subject, relation, object) touching that node with a
 * per-triple delete button.
 */
export default function NodeDetails({
  userId,
  node,
  edges,
  nodes,
  onClose,
  onDeleted,
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [deleting, setDeleting] = useState(null);

  const typeOf = useMemo(() => {
    const m = {};
    for (const n of nodes) m[n.id] = n.type;
    return m;
  }, [nodes]);

  const related = useMemo(() => {
    if (!node) return [];
    return edges.filter(
      (e) =>
        (typeof e.source === "object" ? e.source.id : e.source) === node.id ||
        (typeof e.target === "object" ? e.target.id : e.target) === node.id,
    );
  }, [edges, node]);

  if (!node) return null;

  async function handleDelete(edge) {
    const s = typeof edge.source === "object" ? edge.source.id : edge.source;
    const t = typeof edge.target === "object" ? edge.target.id : edge.target;
    const ok = await confirm({
      title: "Delete this fact?",
      body: (
        <>
          <code style={{ background: "var(--bg-2)", padding: "2px 6px", borderRadius: 4 }}>
            {s} {edge.label} {t}
          </code>
          <div style={{ marginTop: 10, color: "var(--text-2)", fontSize: 12 }}>
            This cannot be undone.
          </div>
        </>
      ),
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    const tripleKey = `${s}|${edge.label}|${t}`;
    setDeleting(tripleKey);
    try {
      await deleteTriple(userId, {
        subject: s,
        relation: edge.label,
        object: t,
        subject_type: typeOf[s] || "other",
        object_type: typeOf[t] || "other",
      });
      toast.success(`Removed: ${s} ${edge.label} ${t}`);
      onDeleted?.();
    } catch (e) {
      toast.error(`Delete failed: ${e.message}`);
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="node-details">
      <div className="node-details-header">
        <div className="node-details-title">
          <span
            className="legend-swatch"
            style={{
              background: TYPE_COLORS[node.type] || TYPE_COLORS.other,
            }}
          />
          <strong>{node.id}</strong>
          <span className="node-type-badge">{node.type}</span>
        </div>
        <button className="btn btn-icon" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="node-details-meta">
        {related.length} fact{related.length === 1 ? "" : "s"} touching this node
      </div>
      <div className="node-details-list">
        {related.length === 0 && (
          <div className="sidebar-empty">No facts touch this node.</div>
        )}
        {related.map((e, i) => {
          const s = typeof e.source === "object" ? e.source.id : e.source;
          const t = typeof e.target === "object" ? e.target.id : e.target;
          const key = `${s}|${e.label}|${t}|${i}`;
          const dKey = `${s}|${e.label}|${t}`;
          const isDeleting = deleting === dKey;
          const focusIsSubject = s === node.id;
          return (
            <div className="node-fact" key={key}>
              <div className="node-fact-line">
                <span
                  className={focusIsSubject ? "node-fact-self" : ""}
                  style={{
                    color: TYPE_COLORS[typeOf[s]] || TYPE_COLORS.other,
                  }}
                >
                  {s}
                </span>
                <span className="node-fact-rel">{e.label}</span>
                <span
                  className={!focusIsSubject ? "node-fact-self" : ""}
                  style={{
                    color: TYPE_COLORS[typeOf[t]] || TYPE_COLORS.other,
                  }}
                >
                  {t}
                </span>
                {(e.valid_from || e.valid_until) && (
                  <span className="node-fact-dates">{fmtDateRange(e)}</span>
                )}
              </div>
              <button
                className="btn-danger"
                onClick={() => handleDelete(e)}
                disabled={isDeleting}
                title="Delete this fact"
              >
                {isDeleting ? "…" : "Delete"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
