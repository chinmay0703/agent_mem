import React, { useEffect, useState } from "react";
import ChatInterface from "./components/Chat/ChatInterface.jsx";
import MemoryGraph from "./components/Graph/MemoryGraph.jsx";
import NodeDetails from "./components/Graph/NodeDetails.jsx";
import Splitter from "./components/Layout/Splitter.jsx";
import ThreadSidebar from "./components/Layout/ThreadSidebar.jsx";
import Modal from "./components/Modal.jsx";
import { ToastProvider, useToast } from "./components/Toast.jsx";
import LandingPage from "./components/Setup/LandingPage.jsx";
import {
  deleteUser,
  fetchGraphStats,
  getSetupStatus,
  resetSetup,
} from "./api/client.js";

function defaultUserId() {
  try {
    const cached = localStorage.getItem("chatmem.userId");
    if (cached) return cached;
  } catch (_) {}
  return "demo-user";
}

const SIDEBAR_MIN = 180;
const SIDEBAR_MAX = 480;
const CHAT_MIN = 320;
const GRAPH_MIN = 320;

function loadLayout() {
  try {
    const v = JSON.parse(localStorage.getItem("chatmem.layout") || "null");
    if (v && typeof v.sidebar === "number" && typeof v.chat === "number") return v;
  } catch (_) {}
  return { sidebar: 240, chat: Math.max(420, Math.floor(window.innerWidth * 0.4)) };
}

function saveLayout(v) {
  try {
    localStorage.setItem("chatmem.layout", JSON.stringify(v));
  } catch (_) {}
}

function AppInner({ onResetConfig }) {
  const [userId, setUserId] = useState(defaultUserId);
  const [draftUserId, setDraftUserId] = useState(userId);
  const [activeThreadId, setActiveThreadId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [threadsKey, setThreadsKey] = useState(0);
  const [filesKey, setFilesKey] = useState(0);
  const [graphData, setGraphData] = useState({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState(null);
  const [graphSearch, setGraphSearch] = useState("");
  const [stats, setStats] = useState({ nodes: 0, edges: 0 });
  const [confirmWipe, setConfirmWipe] = useState(false);
  const [confirmResetCreds, setConfirmResetCreds] = useState(false);
  const [resettingCreds, setResettingCreds] = useState(false);
  const [layout, setLayout] = useState(loadLayout);
  const toast = useToast();

  // Drag handlers for the two splitters. The graph column gets whatever
  // space is left over after sidebar + chat. Persist on every commit so
  // a refresh keeps the user's chosen layout.
  function dragSidebar(dx) {
    setLayout((cur) => {
      const next = Math.max(
        SIDEBAR_MIN,
        Math.min(SIDEBAR_MAX, cur.sidebar + dx),
      );
      const remaining = window.innerWidth - next - 8; // 8 = 2 splitters
      const chatCapped = Math.max(
        CHAT_MIN,
        Math.min(cur.chat, remaining - GRAPH_MIN),
      );
      const v = { sidebar: next, chat: chatCapped };
      saveLayout(v);
      return v;
    });
  }
  function dragChat(dx) {
    setLayout((cur) => {
      const remaining = window.innerWidth - cur.sidebar - 8;
      const next = Math.max(
        CHAT_MIN,
        Math.min(remaining - GRAPH_MIN, cur.chat + dx),
      );
      const v = { sidebar: cur.sidebar, chat: next };
      saveLayout(v);
      return v;
    });
  }

  useEffect(() => {
    let cancel = false;
    async function load() {
      try {
        const s = await fetchGraphStats(userId);
        if (!cancel) setStats(s);
      } catch (_) {
        if (!cancel) setStats({ nodes: 0, edges: 0 });
      }
    }
    load();
    return () => {
      cancel = true;
    };
  }, [userId, refreshKey]);

  // Reclamp panel widths if the window is resized below the current layout's
  // total. Shrinks chat first, then sidebar, so the graph column keeps at
  // least GRAPH_MIN. We only fire when constraints are actually violated —
  // resizing UP leaves the user's chosen layout alone.
  useEffect(() => {
    function handleResize() {
      setLayout((cur) => {
        const total = window.innerWidth - 8; // two 4px splitters
        if (cur.sidebar + cur.chat + GRAPH_MIN <= total) return cur;
        const sidebar = Math.max(
          SIDEBAR_MIN,
          Math.min(cur.sidebar, total - CHAT_MIN - GRAPH_MIN),
        );
        const chat = Math.max(CHAT_MIN, total - sidebar - GRAPH_MIN);
        const v = { sidebar, chat };
        saveLayout(v);
        return v;
      });
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  function applyUser(e) {
    e?.preventDefault();
    const u = (draftUserId || "").trim();
    if (!u) return;
    setUserId(u);
    setActiveThreadId(null);
    setSelectedNode(null);
    try {
      localStorage.setItem("chatmem.userId", u);
    } catch (_) {}
  }

  function startNewThread() {
    setActiveThreadId(null);
    setThreadsKey((k) => k + 1);
  }

  function onTurnComplete() {
    setRefreshKey((k) => k + 1);
    setThreadsKey((k) => k + 1);
    setFilesKey((k) => k + 1);
  }

  function onFilesChanged() {
    setRefreshKey((k) => k + 1);
    setFilesKey((k) => k + 1);
  }

  async function handleWipeUser() {
    setConfirmWipe(false);
    try {
      const res = await deleteUser(userId);
      toast.success(
        `Wiped: ${res.graph_nodes_removed} nodes · ${res.threads_removed} threads`,
      );
      setActiveThreadId(null);
      setSelectedNode(null);
      setRefreshKey((k) => k + 1);
      setThreadsKey((k) => k + 1);
    } catch (e) {
      toast.error(`Wipe failed: ${e.message}`);
    }
  }

  async function handleResetCreds() {
    setResettingCreds(true);
    try {
      await resetSetup();
      toast.success("Credentials cleared — back to setup.");
      setConfirmResetCreds(false);
      // Tell AppGate to flip back to wizard mode. The wizard mounts fresh
      // and re-fetches /setup/status (which is now `configured: false`).
      onResetConfig?.();
    } catch (e) {
      toast.error(`Reset failed: ${e.message}`);
    } finally {
      setResettingCreds(false);
    }
  }

  return (
    <div
      className="app"
      style={{
        gridTemplateColumns:
          `${layout.sidebar}px 4px ${layout.chat}px 4px minmax(0, 1fr)`,
      }}
    >
      <ThreadSidebar
        userId={userId}
        activeThreadId={activeThreadId}
        refreshKey={threadsKey}
        filesRefreshKey={filesKey}
        onFilesChanged={onFilesChanged}
        onSelect={(id) => {
          setActiveThreadId(id);
          setSelectedNode(null);
        }}
        onNew={startNewThread}
      />
      <Splitter onDelta={dragSidebar} ariaLabel="Resize threads panel" />
      <div className="panel">
        <div className="panel-header">
          <h2>
            <span className="dot" />
            Chat
          </h2>
          <form className="user-input" onSubmit={applyUser}>
            <input
              value={draftUserId}
              onChange={(e) => setDraftUserId(e.target.value)}
              placeholder="user_id"
            />
            <button type="submit" className="btn">
              Switch
            </button>
            <button
              type="button"
              className="btn btn-danger-outline"
              onClick={() => setConfirmWipe(true)}
              title="Permanently delete every thread, message, and graph fact for this user"
            >
              Wipe user
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setConfirmResetCreds(true)}
              title="Wipe saved OpenAI / Postgres / Neo4j credentials and re-run the setup wizard"
            >
              Reset credentials
            </button>
          </form>
        </div>
        <ChatInterface
          userId={userId}
          activeThreadId={activeThreadId}
          onTurnComplete={onTurnComplete}
          onThreadCreated={setActiveThreadId}
        />
      </div>
      <Splitter onDelta={dragChat} ariaLabel="Resize chat panel" />
      <div className="panel">
        <div className="panel-header">
          <h2>
            <span
              className="dot"
              style={{
                background: "var(--preference)",
                boxShadow: "0 0 0 4px rgba(138, 78, 224, 0.18)",
              }}
            />
            Memory Graph
            <span className="stats-badge" title="nodes / edges in this user's graph">
              {stats.nodes}n · {stats.edges}e
            </span>
          </h2>
          <div className="user-input">
            <input
              value={graphSearch}
              onChange={(e) => setGraphSearch(e.target.value)}
              placeholder="search nodes..."
              style={{ width: 140 }}
            />
            <button className="btn" onClick={() => setRefreshKey((k) => k + 1)}>
              Refresh
            </button>
          </div>
        </div>
        <div className="graph-and-details">
          <MemoryGraph
            userId={userId}
            refreshKey={refreshKey}
            search={graphSearch}
            onNodeClick={setSelectedNode}
            onDataLoaded={setGraphData}
          />
          {selectedNode && (
            <NodeDetails
              userId={userId}
              node={selectedNode}
              edges={graphData.edges}
              nodes={graphData.nodes}
              onClose={() => setSelectedNode(null)}
              onDeleted={() => {
                setRefreshKey((k) => k + 1);
                // Keep panel open so the user can keep cleaning up; if the
                // last fact for this node is gone, close.
                const stillExists = graphData.edges.some(
                  (e) => e.source === selectedNode.id || e.target === selectedNode.id,
                );
                if (!stillExists) setSelectedNode(null);
              }}
            />
          )}
        </div>
      </div>

      <Modal
        open={confirmWipe}
        title="Wipe all data for this user?"
        onClose={() => setConfirmWipe(false)}
        footer={
          <>
            <button className="btn" onClick={() => setConfirmWipe(false)}>
              Cancel
            </button>
            <button className="btn btn-danger" onClick={handleWipeUser}>
              Yes, wipe everything
            </button>
          </>
        }
      >
        <p>
          This will permanently delete <strong>every thread, message, and
          memory fact</strong> for <code>{userId}</code>. It cannot be undone.
        </p>
      </Modal>

      <Modal
        open={confirmResetCreds}
        title="Reset credentials and re-run setup?"
        onClose={() => !resettingCreds && setConfirmResetCreds(false)}
        footer={
          <>
            <button
              className="btn"
              onClick={() => setConfirmResetCreds(false)}
              disabled={resettingCreds}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={handleResetCreds}
              disabled={resettingCreds}
            >
              {resettingCreds ? "Resetting…" : "Yes, take me to setup"}
            </button>
          </>
        }
      >
        <p>
          This clears the saved <strong>OpenAI key, Postgres, and Neo4j
          credentials</strong> on this machine and returns you to the setup
          wizard.
        </p>
        <p style={{ color: "var(--text-2)", marginTop: 6 }}>
          Your Postgres data and Neo4j graph are <strong>not</strong> touched —
          re-enter the same credentials and you'll pick up exactly where you
          left off.
        </p>
      </Modal>
    </div>
  );
}

function AppGate() {
  // null = unknown (loading), true = configured, false = needs wizard.
  const [configured, setConfigured] = useState(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const s = await getSetupStatus();
        if (!cancel) setConfigured(!!s.configured);
      } catch (_) {
        // Backend unreachable — show the wizard so the user has a path
        // forward instead of an indefinite spinner.
        if (!cancel) setConfigured(false);
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  if (configured === null) {
    return (
      <div
        style={{
          position: "fixed",
          inset: 0,
          display: "grid",
          placeItems: "center",
          background: "var(--bg-0)",
          color: "var(--text-2)",
          fontSize: 13,
        }}
      >
        Loading…
      </div>
    );
  }
  if (!configured) return <LandingPage onComplete={() => setConfigured(true)} />;
  return <AppInner onResetConfig={() => setConfigured(false)} />;
}

export default function App() {
  return (
    <ToastProvider>
      <AppGate />
    </ToastProvider>
  );
}
