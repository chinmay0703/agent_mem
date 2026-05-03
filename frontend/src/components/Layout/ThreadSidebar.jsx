import React, { useEffect, useState } from "react";
import { listThreads, deleteThread, getThread } from "../../api/client.js";
import { useConfirm } from "../Confirm.jsx";
import { useToast } from "../Toast.jsx";
import FilesPanel from "../Files/FilesPanel.jsx";

function relativeTime(iso) {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
}

function deriveTitle(thread) {
  if (thread.title) return thread.title;
  if (thread.summary && thread.summary.length > 0) {
    return thread.summary.slice(0, 60) + (thread.summary.length > 60 ? "…" : "");
  }
  return "New conversation";
}

function downloadFile(name, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ThreadSidebar({
  userId,
  activeThreadId,
  refreshKey,
  onSelect,
  onNew,
  filesRefreshKey,
  onFilesChanged,
}) {
  const [threads, setThreads] = useState([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("threads");
  const toast = useToast();
  const confirm = useConfirm();

  useEffect(() => {
    let cancel = false;
    async function load() {
      setLoading(true);
      try {
        const data = await listThreads(userId);
        if (!cancel) setThreads(data);
      } catch (e) {
        if (!cancel) setThreads([]);
      } finally {
        if (!cancel) setLoading(false);
      }
    }
    load();
    return () => {
      cancel = true;
    };
  }, [userId, refreshKey]);

  async function handleDelete(e, threadId) {
    e.stopPropagation();
    const ok = await confirm({
      title: "Delete this thread?",
      body: "Messages and any memory facts created in this thread will be removed permanently. This cannot be undone.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await deleteThread(userId, threadId);
      setThreads((ts) => ts.filter((t) => t.id !== threadId));
      if (activeThreadId === threadId) onNew?.();
      toast.success(
        `Thread deleted${res?.edges_removed ? ` · ${res.edges_removed} graph edges removed` : ""}`,
      );
    } catch (e) {
      toast.error(`Delete failed: ${e.message}`);
    }
  }

  async function handleExport(e, threadId, kind) {
    e.stopPropagation();
    try {
      const detail = await getThread(userId, threadId);
      if (kind === "json") {
        downloadFile(
          `thread-${threadId}.json`,
          JSON.stringify(detail, null, 2),
          "application/json",
        );
      } else {
        const lines = [
          `# Thread ${detail.id}`,
          `_user: ${userId} · created: ${detail.created_at}_`,
          "",
        ];
        for (const m of detail.messages) {
          lines.push(`### ${m.role === "assistant" ? "Bot" : "User"}`);
          lines.push(m.content);
          lines.push("");
        }
        downloadFile(`thread-${threadId}.md`, lines.join("\n"), "text/markdown");
      }
      toast.success(`Exported as ${kind.toUpperCase()}`);
    } catch (e) {
      toast.error(`Export failed: ${e.message}`);
    }
  }

  return (
    <div className="sidebar">
      <div className="sidebar-tabs">
        <button
          className={`sidebar-tab${tab === "threads" ? " active" : ""}`}
          onClick={() => setTab("threads")}
        >
          Threads
        </button>
        <button
          className={`sidebar-tab${tab === "files" ? " active" : ""}`}
          onClick={() => setTab("files")}
        >
          Files
        </button>
      </div>
      {tab === "files" ? (
        <FilesPanel
          userId={userId}
          threadId={activeThreadId}
          refreshKey={filesRefreshKey}
          onChanged={onFilesChanged}
        />
      ) : (
        <>
      <div className="sidebar-header">
        <h2>Threads</h2>
        <button className="btn btn-icon" onClick={onNew} title="New conversation">
          + New
        </button>
      </div>
      <div className="sidebar-list">
        {loading && threads.length === 0 && (
          <div className="sidebar-empty">Loading…</div>
        )}
        {!loading && threads.length === 0 && (
          <div className="sidebar-empty">No threads yet. Start chatting.</div>
        )}
        {threads.map((t) => (
          <div className="thread-item-row" key={t.id}>
            <button
              className={`thread-item${activeThreadId === t.id ? " active" : ""}`}
              onClick={() => onSelect(t.id)}
              title={t.summary || t.id}
            >
              <div className="thread-title">{deriveTitle(t)}</div>
              <div className="thread-meta">
                <span>{relativeTime(t.updated_at)}</span>
                <span>{t.message_count} msg</span>
              </div>
            </button>
            <div className="thread-actions">
              <button
                className="thread-action"
                onClick={(e) => handleExport(e, t.id, "md")}
                title="Export as Markdown"
                aria-label="Export as Markdown"
              >
                ⤓
              </button>
              <button
                className="thread-action thread-delete"
                onClick={(e) => handleDelete(e, t.id)}
                title="Delete thread"
                aria-label="Delete thread"
              >
                ×
              </button>
            </div>
          </div>
        ))}
      </div>
        </>
      )}
    </div>
  );
}
