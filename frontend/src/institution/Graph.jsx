import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";

const NODE = { high: "#ef476f", medium: "#ffb020", low: "#1e9bf0" };

// Lay each connected cluster out on its own and tile them on a grid, so separate patterns stay separate.
function layoutClusters(cy) {
  const comps = cy.elements().components().sort((a, b) => b.nodes().length - a.nodes().length);
  const boxes = comps.map((c) => {
    const n = c.nodes().length;
    const r = n === 1 ? 22 : n <= 3 ? 26 + 14 * n : 40 + 26 * Math.sqrt(n);
    return { c, n, r };
  });
  const totalArea = boxes.reduce((s, b) => s + (2 * b.r + 70) ** 2, 0);
  const aspect = Math.max(0.8, cy.width() / Math.max(1, cy.height()));
  const rowWidth = Math.max(300, Math.sqrt(totalArea * aspect) * 1.08);
  let x = 0, y = 0, rowH = 0;
  for (const b of boxes) {
    const w = 2 * b.r + 70;
    if (x > 0 && x + w > rowWidth) { x = 0; y += rowH; rowH = 0; }
    const bb = { x1: x + 35, y1: y + 35, w: 2 * b.r, h: 2 * b.r };
    const nodes = b.c.nodes();
    const deg = (nd) => nd.degree(false);
    const hub = nodes.max(deg);
    const edges = b.c.edges().length;
    const isCycle = b.n >= 3 && edges >= b.n && nodes.every((nd) => nd.connectedEdges().length === 2);
    const isChain = b.n >= 3 && edges === b.n - 1 && hub.value <= 2;
    let opts;
    if (b.n === 1) opts = { name: "grid", boundingBox: bb };
    else if (isCycle) opts = { name: "circle", boundingBox: bb };
    else if (isChain) {
      const root = nodes.filter((nd) => nd.indegree(false) === 0)[0] || nodes[0];
      opts = { name: "breadthfirst", directed: true, roots: root, boundingBox: { ...bb, w: bb.w * 0.6, x1: bb.x1 + bb.w * 0.2 }, spacingFactor: 0.9 };
    } else if (b.n >= 5 && hub.value >= 0.5 * (b.n - 1)) {
      opts = { name: "concentric", boundingBox: bb, concentric: (nd) => (nd.same(hub.ele) ? 10 : nd.edgesWith(hub.ele).length ? 5 : 1), levelWidth: () => 1, minNodeSpacing: 14, startAngle: Math.PI * 1.5 };
    } else opts = { name: "cose", boundingBox: bb, animate: false, randomize: true, nodeRepulsion: () => 8000, idealEdgeLength: () => 55, numIter: 800 };
    b.c.layout({ ...opts, fit: false, animate: false, padding: 0 }).run();
    x += w;
    rowH = Math.max(rowH, w);
  }
}

// Ego network: focus account in the centre, one ring per hop.
function layoutEgo(cy) {
  const focus = cy.nodes("[?focus]");
  const depth = {};
  cy.elements().bfs({ roots: focus, directed: false, visit: (v, e, u, i, d) => { depth[v.id()] = d; } });
  cy.layout({ name: "concentric", concentric: (n) => 10 - (depth[n.id()] ?? 9), levelWidth: () => 1, minNodeSpacing: 26, spacingFactor: 1.15, animate: false, fit: true, padding: 30 }).run();
}

export function GraphLegend() {
  return (
    <div className="legend">
      <span><i style={{ background: NODE.high }} />high priority</span>
      <span><i style={{ background: NODE.medium }} />medium</span>
      <span><i style={{ background: NODE.low }} />low</span>
      <span><i style={{ background: "#9fc9c4" }} />not alerted</span>
      <span><i style={{ background: "#fff", boxShadow: "0 0 0 3px #ff6b4a" }} />focus account</span>
      <span>edge width = total amount · arrows = direction of funds</span>
    </div>
  );
}

export default function Graph({ data, onSelect, height = 540 }) {
  const ref = useRef();
  const cyRef = useRef();
  useEffect(() => {
    if (!data) return;
    const maxAmt = Math.max(1, ...data.edges.map((e) => e.amount));
    const maxNode = Math.max(1, ...data.nodes.map((n) => n.in_amount + n.out_amount));
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...data.nodes.map((n) => ({ data: { ...n, label: n.id.replace("ACC-", ""), size: 20 + 22 * Math.sqrt((n.in_amount + n.out_amount) / maxNode) } })),
        ...data.edges.map((e, i) => ({ data: { id: `e${i}`, ...e, w: 1 + 7 * Math.sqrt(e.amount / maxAmt) } })),
      ],
      style: [
        { selector: "node", style: { "background-color": "#9fc9c4", width: "data(size)", height: "data(size)", label: "data(label)", "font-size": 9, color: "#10292d", "text-valign": "bottom", "text-margin-y": 4, "font-family": "Space Grotesk, sans-serif", "border-width": 2, "border-color": "#fff", "min-zoomed-font-size": 7 } },
        { selector: "node[priority = 'high']", style: { "background-color": NODE.high } },
        { selector: "node[priority = 'medium']", style: { "background-color": NODE.medium } },
        { selector: "node[priority = 'low']", style: { "background-color": NODE.low } },
        { selector: "node[?focus]", style: { "border-width": 5, "border-color": "#ff6b4a", "font-weight": 800, "font-size": 11 } },
        { selector: "edge", style: { width: "data(w)", "line-color": "#b8c9d6", "target-arrow-color": "#8aa4b8", "target-arrow-shape": "triangle", "curve-style": "bezier", opacity: 0.8, "arrow-scale": 0.8 } },
        { selector: ".faded", style: { opacity: 0.12 } },
        { selector: "edge.hl", style: { "line-color": "#ff6b4a", "target-arrow-color": "#ff6b4a", opacity: 1 } },
        { selector: "node:selected", style: { "border-color": "#0c2340", "border-width": 4 } },
      ],
      layout: { name: "preset" },
      userZoomingEnabled: false, // page scroll must not zoom the graph; use the buttons (as in MuleGuard)
    });
    cy.on("mouseover", "node", (e) => {
      const n = e.target.closedNeighborhood();
      cy.elements().not(n).addClass("faded");
      n.edges().addClass("hl");
    });
    cy.on("mouseout", "node", () => cy.elements().removeClass("faded hl"));
    cy.on("tap", "node", (e) => onSelect?.(e.target.data()));
    if (data.mode === "ego" && cy.nodes("[?focus]").length) layoutEgo(cy);
    else layoutClusters(cy);
    cy.fit(undefined, 30);
    // gentle entrance animation
    cy.nodes().style("opacity", 0);
    cy.nodes().forEach((n, i) => setTimeout(() => !cy.destroyed() && n.animate({ style: { opacity: 1 } }, { duration: 300, complete: () => n.removeStyle("opacity") }), Math.min(600, i * 8)));
    const ro = new ResizeObserver(() => { if (!cy.destroyed()) { cy.resize(); cy.fit(undefined, 30); } });
    ro.observe(ref.current);
    cy.on("destroy", () => ro.disconnect());
    cyRef.current = cy;
    return () => { cy.elements().stop(true, false); ro.disconnect(); cy.destroy(); };
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const zoom = (k) => { const cy = cyRef.current; if (cy) cy.animate({ zoom: { level: cy.zoom() * k, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } } }, { duration: 200 }); };
  return (
    <div style={{ position: "relative" }}>
      <div ref={ref} className="graph-box" style={{ height }} />
      <div style={{ position: "absolute", right: 12, top: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        <button className="small subtle" title="Zoom in" onClick={() => zoom(1.3)}>＋</button>
        <button className="small subtle" title="Zoom out" onClick={() => zoom(1 / 1.3)}>－</button>
        <button className="small subtle" title="Fit to view" onClick={() => cyRef.current?.animate({ fit: { padding: 30 } }, { duration: 250 })}>⤢</button>
      </div>
    </div>
  );
}
