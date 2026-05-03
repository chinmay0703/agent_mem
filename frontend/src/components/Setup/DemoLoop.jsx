import React, { useEffect, useRef, useState } from "react";

// A scripted, looping mini-demo of the actual chat → graph pipeline.
// Three pre-canned turns play in sequence; each one types out a user
// question, then the bot reply, then animates the freshly "extracted"
// nodes + edges into the mini knowledge graph below.
//
// Coordinates are hand-placed (no force layout) so the demo lays out the
// same way every time and viewers see the structure clearly.

const DEMO = [
  {
    user: "Hi! I'm Maya, a data scientist at Stripe in Bangalore.",
    bot: "Welcome, Maya. Got it — Stripe, Bangalore, data science.",
    nodes: [
      { id: "maya", label: "Maya", type: "user", x: 60, y: 110 },
      { id: "stripe", label: "Stripe", type: "company", x: 165, y: 50 },
      { id: "bangalore", label: "Bangalore", type: "topic", x: 175, y: 165 },
      { id: "datasci", label: "Data Science", type: "preference", x: 35, y: 195 },
    ],
    edges: [
      { from: "maya", to: "stripe", label: "WORKS_AT" },
      { from: "maya", to: "bangalore", label: "LIVES_IN" },
      { from: "maya", to: "datasci", label: "WORKS_IN" },
    ],
  },
  {
    user: "I'm planning to move to Berlin next month.",
    bot: "Noted — Berlin, with valid_from set to next month.",
    nodes: [{ id: "berlin", label: "Berlin", type: "goal", x: 270, y: 110 }],
    edges: [{ from: "maya", to: "berlin", label: "PLANS_MOVE_TO" }],
  },
  {
    user: "My brother Arjun is a doctor.",
    bot: "Got it — Arjun is your brother, and he's a doctor.",
    nodes: [
      { id: "arjun", label: "Arjun", type: "person", x: 245, y: 30 },
      { id: "doctor", label: "Doctor", type: "topic", x: 285, y: 175 },
    ],
    edges: [
      { from: "maya", to: "arjun", label: "HAS_BROTHER" },
      { from: "arjun", to: "doctor", label: "IS_A" },
    ],
  },
];

const TYPE_USER_MS = 26;
const TYPE_BOT_MS = 18;
const PAUSE_THINK_MS = 700;
const PAUSE_AFTER_BOT_MS = 350;
const REVEAL_NODE_MS = 260;
const REVEAL_EDGE_MS = 160;
const PAUSE_BETWEEN_TURNS_MS = 2200;
const PAUSE_BEFORE_RESTART_MS = 2800;

export default function DemoLoop() {
  const [turn, setTurn] = useState(0);
  const [phase, setPhase] = useState("idle"); // idle|user|think|bot|extract|hold
  const [userTyped, setUserTyped] = useState("");
  const [botTyped, setBotTyped] = useState("");
  // Cumulative across all turns until restart.
  const [revealedNodes, setRevealedNodes] = useState([]);
  const [revealedEdges, setRevealedEdges] = useState([]);

  // Single owner of the timeline so re-mounts cleanly cancel pending work.
  useEffect(() => {
    let cancelled = false;
    const timeouts = [];
    function delay(ms) {
      return new Promise((resolve) => {
        const id = setTimeout(() => {
          if (!cancelled) resolve();
        }, ms);
        timeouts.push(id);
      });
    }

    async function loop() {
      while (!cancelled) {
        // Fresh loop — clear graph and start at turn 0.
        setRevealedNodes([]);
        setRevealedEdges([]);

        for (let i = 0; i < DEMO.length && !cancelled; i++) {
          const t = DEMO[i];
          setTurn(i);
          setUserTyped("");
          setBotTyped("");

          // 1. type user message char-by-char
          setPhase("user");
          for (let c = 1; c <= t.user.length; c++) {
            if (cancelled) return;
            setUserTyped(t.user.slice(0, c));
            await delay(TYPE_USER_MS);
          }

          // 2. brief "thinking" pause — mirrors the real agent's gap
          setPhase("think");
          await delay(PAUSE_THINK_MS);

          // 3. type bot reply
          setPhase("bot");
          for (let c = 1; c <= t.bot.length; c++) {
            if (cancelled) return;
            setBotTyped(t.bot.slice(0, c));
            await delay(TYPE_BOT_MS);
          }
          await delay(PAUSE_AFTER_BOT_MS);

          // 4. spawn nodes one at a time, then edges
          setPhase("extract");
          for (const node of t.nodes) {
            if (cancelled) return;
            setRevealedNodes((prev) => [...prev, node]);
            await delay(REVEAL_NODE_MS);
          }
          for (const edge of t.edges) {
            if (cancelled) return;
            setRevealedEdges((prev) => [...prev, edge]);
            await delay(REVEAL_EDGE_MS);
          }

          // 5. hold so the viewer can read the result
          setPhase("hold");
          const wait =
            i === DEMO.length - 1
              ? PAUSE_BEFORE_RESTART_MS
              : PAUSE_BETWEEN_TURNS_MS;
          await delay(wait);
        }
      }
    }
    loop();

    return () => {
      cancelled = true;
      timeouts.forEach(clearTimeout);
    };
  }, []);

  const t = DEMO[turn];
  const showUserBubble = phase !== "idle";
  const showBotBubble =
    phase === "bot" || phase === "extract" || phase === "hold";
  const nodeIndex = new Map(revealedNodes.map((n) => [n.id, n]));

  return (
    <div className="setup-demo">
      <div className="setup-demo-head">
        <span className="setup-demo-pulse" />
        <span>Live demo</span>
        <span className="setup-demo-counter">
          turn {turn + 1} / {DEMO.length}
        </span>
      </div>

      <div className="setup-demo-chat">
        {showUserBubble && (
          <div className="setup-demo-row right">
            <div className="setup-demo-bubble user">
              {userTyped}
              {phase === "user" && <span className="setup-demo-caret" />}
            </div>
          </div>
        )}
        {phase === "think" && (
          <div className="setup-demo-row left">
            <div className="setup-demo-bubble bot thinking">
              <span className="setup-demo-dot" />
              <span className="setup-demo-dot" />
              <span className="setup-demo-dot" />
            </div>
          </div>
        )}
        {showBotBubble && (
          <div className="setup-demo-row left">
            <div className="setup-demo-bubble bot">
              {botTyped}
              {phase === "bot" && <span className="setup-demo-caret" />}
            </div>
          </div>
        )}
      </div>

      <div className="setup-demo-graph-wrap">
        <svg
          viewBox="0 0 320 230"
          className="setup-demo-graph"
          aria-hidden
        >
          {/* edges first so they sit beneath nodes */}
          <g className="setup-demo-edges">
            {revealedEdges.map((e, i) => {
              const a = nodeIndex.get(e.from);
              const b = nodeIndex.get(e.to);
              if (!a || !b) return null;
              const mx = (a.x + b.x) / 2;
              const my = (a.y + b.y) / 2;
              return (
                <g key={`${e.from}-${e.to}-${i}`} className="setup-demo-edge-grp">
                  <line
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke="var(--border-strong)"
                    strokeWidth="1.5"
                  />
                  <text
                    x={mx}
                    y={my - 3}
                    textAnchor="middle"
                    className="setup-demo-edge-label"
                  >
                    {e.label}
                  </text>
                </g>
              );
            })}
          </g>
          <g className="setup-demo-nodes">
            {revealedNodes.map((n, i) => {
              const isNew =
                phase === "extract" &&
                i >= revealedNodes.length - t.nodes.length;
              return (
                <g
                  key={n.id}
                  className={`setup-demo-node-grp ${isNew ? "fresh" : ""}`}
                  transform={`translate(${n.x},${n.y})`}
                >
                  <circle
                    r="9"
                    style={{ fill: `var(--${n.type})` }}
                    className="setup-demo-node"
                  />
                  <text
                    y="22"
                    textAnchor="middle"
                    className="setup-demo-node-label"
                  >
                    {n.label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="setup-demo-foot">
        <div className="setup-demo-progress">
          {DEMO.map((_, i) => (
            <span
              key={i}
              className={`setup-demo-tick ${
                i < turn ? "done" : i === turn ? "active" : ""
              }`}
            />
          ))}
        </div>
        <span className="setup-demo-caption">
          Real bot, real graph — this is what happens every turn.
        </span>
      </div>
    </div>
  );
}
