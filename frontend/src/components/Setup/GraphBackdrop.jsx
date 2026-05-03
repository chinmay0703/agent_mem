import React, { useEffect, useMemo, useRef } from "react";

// Animated, cursor-reactive knowledge-graph backdrop. Renders behind the
// wizard so the user sees a visual hint of what this app does — graph
// memory — without us having to draw real data.
//
// Implementation notes:
//  - Nodes drift on a slow per-axis sin/cos so the layout breathes.
//  - The cursor pushes nearby nodes away (and they grow on approach), so
//    the backdrop feels alive without intercepting clicks — pointer
//    events stay disabled on the SVG; we listen on `window` instead.
//  - Edges connect any pair within MAX_LINK_DIST. Recomputed every frame
//    so connectivity ebbs and flows as nodes drift / get pushed.
//  - Colors come from project CSS vars (--accent, --preference, etc.) so
//    the backdrop matches the rest of the UI.
//  - Animation pauses if the user prefers reduced motion.

const NODE_COUNT = 32;
const MAX_LINK_DIST = 240;          // px — anything closer than this gets an edge
const REPULSE_RADIUS = 200;         // px — cursor reach
const REPULSE_STRENGTH = 75;        // px — max push when right under cursor
const HOVER_SCALE_MAX = 0.7;        // 70% extra size at center of cursor
const COLORS = ["--accent", "--preference", "--company", "--topic", "--goal", "--person"];

function rand(min, max) {
  return min + Math.random() * (max - min);
}

export default function GraphBackdrop() {
  const svgRef = useRef(null);
  const linesRef = useRef(null);
  const nodesRef = useRef(null);

  // One stable set of node "seeds" per mount. Each seed defines the
  // node's home position and its drift parameters; positions are computed
  // each frame from these.
  const seeds = useMemo(() => {
    const w = typeof window !== "undefined" ? window.innerWidth : 1400;
    const h = typeof window !== "undefined" ? window.innerHeight : 900;
    return Array.from({ length: NODE_COUNT }, (_, i) => ({
      id: i,
      x0: rand(0.05, 0.95) * w,
      y0: rand(0.05, 0.95) * h,
      ax: rand(20, 60),
      ay: rand(15, 50),
      sx: rand(0.0003, 0.0008),
      sy: rand(0.00025, 0.0007),
      px: rand(0, Math.PI * 2),
      py: rand(0, Math.PI * 2),
      r: rand(5, 11),
      color: COLORS[i % COLORS.length],
    }));
  }, []);

  useEffect(() => {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let raf = 0;
    let stopped = false;

    function setSize() {
      const svg = svgRef.current;
      if (!svg) return;
      svg.setAttribute("viewBox", `0 0 ${window.innerWidth} ${window.innerHeight}`);
    }
    setSize();
    window.addEventListener("resize", setSize);

    // Track the cursor in viewport pixels. We listen on `window` (not the
    // SVG itself) because the SVG has pointer-events: none so the wizard
    // form stays clickable. .active toggles off if the cursor leaves the
    // window, so nodes settle back to their drift positions.
    const mouse = { x: -9999, y: -9999, active: false };
    function onMove(e) {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      mouse.active = true;
    }
    function onLeave() {
      mouse.active = false;
      mouse.x = -9999;
      mouse.y = -9999;
    }
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("mouseleave", onLeave);
    document.addEventListener("mouseleave", onLeave);

    const positions = seeds.map((s) => ({ x: s.x0, y: s.y0, r: s.r }));

    function frame(t) {
      if (stopped) return;
      // Update positions: drift, then apply cursor repulsion + hover scale.
      for (let i = 0; i < seeds.length; i++) {
        const s = seeds[i];
        let x = s.x0 + Math.sin(t * s.sx + s.px) * s.ax;
        let y = s.y0 + Math.cos(t * s.sy + s.py) * s.ay;
        let r = s.r;
        if (mouse.active) {
          const dx = x - mouse.x;
          const dy = y - mouse.y;
          const d = Math.hypot(dx, dy);
          if (d < REPULSE_RADIUS && d > 0.01) {
            const t01 = (REPULSE_RADIUS - d) / REPULSE_RADIUS; // 0..1
            const ease = t01 * t01; // quadratic — falloff feels natural
            x += (dx / d) * REPULSE_STRENGTH * ease;
            y += (dy / d) * REPULSE_STRENGTH * ease;
            r = s.r * (1 + HOVER_SCALE_MAX * ease);
          }
        }
        positions[i].x = x;
        positions[i].y = y;
        positions[i].r = r;
      }
      // Update node DOM.
      const nodes = nodesRef.current?.children;
      if (nodes) {
        for (let i = 0; i < nodes.length; i++) {
          const n = nodes[i];
          n.setAttribute("cx", positions[i].x.toFixed(1));
          n.setAttribute("cy", positions[i].y.toFixed(1));
          n.setAttribute("r", positions[i].r.toFixed(1));
        }
      }
      // Update edges — clear + rebuild so connectivity reflects current proximity.
      const linesEl = linesRef.current;
      if (linesEl) {
        let html = "";
        for (let i = 0; i < positions.length; i++) {
          for (let j = i + 1; j < positions.length; j++) {
            const dx = positions[i].x - positions[j].x;
            const dy = positions[i].y - positions[j].y;
            const d2 = dx * dx + dy * dy;
            const max2 = MAX_LINK_DIST * MAX_LINK_DIST;
            if (d2 < max2) {
              const opacity = (1 - Math.sqrt(d2) / MAX_LINK_DIST) * 0.42;
              html +=
                `<line x1="${positions[i].x.toFixed(1)}" y1="${positions[i].y.toFixed(1)}" ` +
                `x2="${positions[j].x.toFixed(1)}" y2="${positions[j].y.toFixed(1)}" ` +
                `stroke="currentColor" stroke-width="1.25" stroke-opacity="${opacity.toFixed(3)}" />`;
            }
          }
        }
        linesEl.innerHTML = html;
      }
      if (!reduce) raf = requestAnimationFrame(frame);
    }
    if (reduce) {
      // Render one static frame.
      frame(0);
    } else {
      raf = requestAnimationFrame(frame);
    }
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", setSize);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("mouseleave", onLeave);
      document.removeEventListener("mouseleave", onLeave);
    };
  }, [seeds]);

  return (
    <svg
      ref={svgRef}
      className="setup-graph-bg"
      aria-hidden
      preserveAspectRatio="none"
    >
      <g ref={linesRef} className="setup-graph-edges" />
      <g ref={nodesRef} className="setup-graph-nodes">
        {seeds.map((s) => (
          <circle
            key={s.id}
            cx={s.x0}
            cy={s.y0}
            r={s.r}
            style={{ fill: `var(${s.color})` }}
          />
        ))}
      </g>
    </svg>
  );
}
