import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const SRC_RE = /\[src:([A-Za-z0-9_\-]+)\]/g;

function srcToShort(srcId, sources) {
  // Map a chunk id to its 1-based position in this message's sources.
  const i = sources.findIndex((s) => s.src_id === srcId);
  return i >= 0 ? i + 1 : null;
}

function srcLabel(s) {
  if (!s) return "";
  if (s.kind === "file") {
    const name = s.filename || "file";
    const page = s.page ? ` p.${s.page}` : "";
    return `${name}${page}`;
  }
  if (s.kind === "message") {
    const when = s.created_at ? new Date(s.created_at).toLocaleDateString() : "";
    return `chat${when ? ` · ${when}` : ""}`;
  }
  return s.src_id || "source";
}

/**
 * Render text replacing [src:CHUNK_ID] markers with citation chips. Returns
 * a list of React children (strings + chip elements) suitable for inline
 * rendering inside any block (paragraph, list item, table cell).
 */
function inlineWithCitations(text, sources, onCite) {
  if (typeof text !== "string") return text;
  if (!sources || sources.length === 0) {
    // Strip dangling markers so we don't show raw [src:...] to the user.
    return [text.replace(SRC_RE, "")];
  }
  const out = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(SRC_RE)) {
    const start = m.index;
    if (start > last) out.push(text.slice(last, start));
    const sid = m[1];
    const idx = srcToShort(sid, sources);
    if (idx) {
      const s = sources[idx - 1];
      out.push(
        <button
          key={`c${key++}`}
          className="cite-chip"
          title={`${srcLabel(s)} — click for details`}
          onClick={(e) => {
            e.preventDefault();
            onCite?.(s);
          }}
        >
          {idx}
        </button>,
      );
    }
    last = start + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Recursively walk children, replacing string nodes with citation chips. */
function injectCitations(children, sources, onCite) {
  if (children == null) return children;
  if (Array.isArray(children)) {
    return children.flatMap((c, i) => {
      const r = injectCitations(c, sources, onCite);
      return Array.isArray(r) ? r : [r];
    });
  }
  if (typeof children === "string") {
    return inlineWithCitations(children, sources, onCite);
  }
  return children;
}

export default function MarkdownView({ children, sources = [], onCite }) {
  const enrich = (node) => injectCitations(node, sources, onCite);
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={{
        a: (props) => (
          <a {...props} target="_blank" rel="noopener noreferrer">
            {enrich(props.children)}
          </a>
        ),
        p: (props) => <p>{enrich(props.children)}</p>,
        li: (props) => <li>{enrich(props.children)}</li>,
        h1: (props) => <h1>{enrich(props.children)}</h1>,
        h2: (props) => <h2>{enrich(props.children)}</h2>,
        h3: (props) => <h3>{enrich(props.children)}</h3>,
        h4: (props) => <h4>{enrich(props.children)}</h4>,
        td: (props) => <td>{enrich(props.children)}</td>,
        th: (props) => <th>{enrich(props.children)}</th>,
        blockquote: (props) => <blockquote>{enrich(props.children)}</blockquote>,
        code({ inline, className, children, ...props }) {
          if (inline) {
            return (
              <code className="md-code-inline" {...props}>
                {children}
              </code>
            );
          }
          return (
            <pre className="md-code-block">
              <code className={className} {...props}>
                {children}
              </code>
            </pre>
          );
        },
      }}
    >
      {children || ""}
    </ReactMarkdown>
  );
}
