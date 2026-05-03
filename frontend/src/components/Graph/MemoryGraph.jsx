import React, { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { fetchGraph } from "../../api/client.js";

const COLORS = {
  user: "#3b6ee8",
  company: "#16a875",
  goal: "#e6862b",
  preference: "#8a4ee0",
  person: "#e0517f",
  topic: "#1ea7c1",
  other: "#6f7c93",
};

const RADIUS = {
  user: 16,
  company: 13,
  goal: 12,
  preference: 12,
  person: 12,
  topic: 11,
  other: 10,
};

export default function MemoryGraph({
  userId,
  refreshKey,
  search = "",
  onNodeClick,
  onDataLoaded,
}) {
  const containerRef = useRef(null);
  const svgRef = useRef(null);
  const tooltipRef = useRef(null);
  const simulationRef = useRef(null);
  const [data, setData] = useState({ nodes: [], edges: [] });
  const [error, setError] = useState(null);

  // Fetch on mount, on user change, and whenever a chat turn completes.
  useEffect(() => {
    let cancel = false;
    async function load() {
      try {
        const res = await fetchGraph(userId);
        if (!cancel) {
          setData(res);
          setError(null);
          onDataLoaded?.(res);
        }
      } catch (e) {
        if (!cancel) setError(e.message);
      }
    }
    load();
    return () => {
      cancel = true;
    };
  }, [userId, refreshKey]);

  // Stable shallow copy: D3 mutates node positions. Apply search filter if
  // present — keep nodes whose id matches AND any node directly connected
  // to a match, plus the edges between them.
  const graph = useMemo(() => {
    const q = (search || "").trim().toLowerCase();
    if (!q) {
      return {
        nodes: data.nodes.map((n) => ({ ...n })),
        edges: data.edges.map((e) => ({ ...e })),
      };
    }
    const matched = new Set(
      data.nodes.filter((n) => n.id.toLowerCase().includes(q)).map((n) => n.id),
    );
    // Expand to neighbors so the matched node has context.
    for (const e of data.edges) {
      if (matched.has(e.source) || matched.has(e.target)) {
        matched.add(e.source);
        matched.add(e.target);
      }
    }
    const nodes = data.nodes.filter((n) => matched.has(n.id)).map((n) => ({ ...n }));
    const edges = data.edges
      .filter((e) => matched.has(e.source) && matched.has(e.target))
      .map((e) => ({ ...e }));
    return { nodes, edges };
  }, [data, search]);

  useEffect(() => {
    const container = containerRef.current;
    const svgEl = svgRef.current;
    if (!container || !svgEl) return;

    const { clientWidth: width, clientHeight: height } = container;
    const svg = d3.select(svgEl).attr("viewBox", [0, 0, width, height]);
    svg.selectAll("*").remove();

    if (graph.nodes.length === 0) {
      simulationRef.current?.stop();
      return;
    }

    // Defs: arrow marker for directed edges.
    const defs = svg.append("defs");
    defs
      .append("marker")
      .attr("id", "arrow")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 18)
      .attr("refY", 0)
      .attr("markerWidth", 8)
      .attr("markerHeight", 8)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", "#a6afc0");

    const root = svg.append("g");

    // Zoom/pan.
    const zoom = d3
      .zoom()
      .scaleExtent([0.25, 4])
      .on("zoom", (ev) => root.attr("transform", ev.transform));
    svg.call(zoom);

    // Links (lines) with edge labels rendered on top.
    const linkGroup = root.append("g").attr("stroke", "#c8d0dc").attr("stroke-opacity", 0.9);
    const link = linkGroup
      .selectAll("line")
      .data(graph.edges)
      .join("line")
      .attr("stroke-width", 1.4)
      .attr("marker-end", "url(#arrow)");

    const labelGroup = root.append("g");
    const linkLabel = labelGroup
      .selectAll("text")
      .data(graph.edges)
      .join("text")
      .attr("font-size", 10)
      .attr("fill", "#6b7488")
      .attr("text-anchor", "middle")
      .attr("paint-order", "stroke")
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 3)
      .attr("stroke-linejoin", "round")
      .text((d) => d.label);

    // Nodes (with subtle outer glow).
    const nodeGroup = root.append("g");
    const node = nodeGroup
      .selectAll("g")
      .data(graph.nodes, (d) => d.id)
      .join("g")
      .attr("cursor", "grab");

    node
      .append("circle")
      .attr("r", (d) => RADIUS[d.type] || RADIUS.other)
      .attr("fill", (d) => COLORS[d.type] || COLORS.other)
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 2)
      .style("filter", (d) => `drop-shadow(0 2px 4px ${COLORS[d.type] || COLORS.other}55)`);

    node
      .append("text")
      .text((d) => d.id)
      .attr("font-size", 11)
      .attr("dy", (d) => (RADIUS[d.type] || RADIUS.other) + 14)
      .attr("text-anchor", "middle")
      .attr("fill", "#1d2433")
      .attr("paint-order", "stroke")
      .attr("stroke", "#ffffff")
      .attr("stroke-width", 3)
      .attr("stroke-linejoin", "round")
      .style("pointer-events", "none");

    // Tooltip.
    const tooltip = d3.select(tooltipRef.current);
    node
      .on("mouseenter", function (ev, d) {
        const rels = graph.edges.filter((e) => e.source.id === d.id || e.target.id === d.id || e.source === d.id || e.target === d.id);
        const lines = [`<strong>${d.id}</strong> · ${d.type}`];
        rels.slice(0, 6).forEach((e) => {
          const s = typeof e.source === "object" ? e.source.id : e.source;
          const t = typeof e.target === "object" ? e.target.id : e.target;
          lines.push(`${s} <em style="color:#6b7488">${e.label}</em> ${t}`);
        });
        if (rels.length > 6) lines.push(`+${rels.length - 6} more…`);
        tooltip.html(lines.join("<br/>")).classed("visible", true);
        d3.select(this).select("circle").attr("stroke", "#1d2433").attr("stroke-width", 2.5);
      })
      .on("mousemove", function (ev) {
        const rect = container.getBoundingClientRect();
        tooltip
          .style("left", `${ev.clientX - rect.left + 12}px`)
          .style("top", `${ev.clientY - rect.top + 12}px`);
      })
      .on("mouseleave", function () {
        tooltip.classed("visible", false);
        d3.select(this).select("circle").attr("stroke", "#ffffff").attr("stroke-width", 2);
      })
      .on("click", function (ev, d) {
        ev.stopPropagation();
        onNodeClick?.(d);
      });

    // Drag.
    const drag = d3
      .drag()
      .on("start", (ev, d) => {
        if (!ev.active) simulationRef.current.alphaTarget(0.25).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (ev, d) => {
        d.fx = ev.x;
        d.fy = ev.y;
      })
      .on("end", (ev, d) => {
        if (!ev.active) simulationRef.current.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
    node.call(drag);

    // Force simulation.
    const sim = d3
      .forceSimulation(graph.nodes)
      .force(
        "link",
        d3.forceLink(graph.edges).id((d) => d.id).distance(110).strength(0.6),
      )
      .force("charge", d3.forceManyBody().strength(-280))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide().radius((d) => (RADIUS[d.type] || RADIUS.other) + 14))
      .on("tick", () => {
        link
          .attr("x1", (d) => d.source.x)
          .attr("y1", (d) => d.source.y)
          .attr("x2", (d) => d.target.x)
          .attr("y2", (d) => d.target.y);
        linkLabel
          .attr("x", (d) => (d.source.x + d.target.x) / 2)
          .attr("y", (d) => (d.source.y + d.target.y) / 2);
        node.attr("transform", (d) => `translate(${d.x},${d.y})`);
      });
    simulationRef.current = sim;

    return () => {
      sim.stop();
    };
  }, [graph]);

  return (
    <div className="graph-wrap" ref={containerRef}>
      <svg ref={svgRef} />
      <div className="tooltip" ref={tooltipRef} />
      <div className="legend">
        {Object.entries(COLORS).map(([k, v]) => (
          <div className="legend-row" key={k}>
            <span className="legend-swatch" style={{ background: v, color: v }} />
            <span style={{ textTransform: "capitalize" }}>{k}</span>
          </div>
        ))}
      </div>
      {graph.nodes.length === 0 && !error && (
        <div className="graph-empty">
          No memory yet. Tell the bot something durable about yourself —
          your job, goals, preferences — and the graph will populate.
        </div>
      )}
      {error && (
        <div className="graph-empty" style={{ color: "#ff8b8b" }}>
          {error}
        </div>
      )}
    </div>
  );
}
