import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { inst, inr } from "../api.js";
import Graph, { GraphLegend } from "./Graph.jsx";

export default function Network({ dsId }) {
  const [g, setG] = useState(null);
  const [sel, setSel] = useState(null);
  const nav = useNavigate();
  useEffect(() => { if (dsId) inst.get(`/datasets/${dsId}/graph?limit=260`).then(setG); }, [dsId]);
  if (!dsId) return <div className="card">Upload a dataset on the <Link to="/institution">Overview</Link> first.</div>;
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Network investigation 🕸️</h1><p className="muted">Each cluster is a flagged account (or a detected ring) with its strongest counterparties. Links between flagged accounts outside a ring appear in each account's own network view. Use ＋/－ to zoom; drag to pan.</p></div><GraphLegend /></div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) 280px", alignItems: "start" }}>
        <div className="card reveal">{g ? <Graph data={g} height={780} onSelect={setSel} /> : <div className="skeleton" style={{ height: 780 }} />}
          {g?.truncated && <p className="small muted">Showing the first {g.nodes.length} nodes (alerted accounts first).</p>}</div>
        <div className="card reveal" style={{ position: "sticky", top: 20 }}>
          {sel ? (<>
            <h2 className="num">{sel.id}</h2>
            {sel.priority ? <span className={`pill ${sel.priority}`}>{sel.priority} priority</span> : <span className="pill">not alerted</span>}
            <dl className="kv small" style={{ marginTop: 12 }}><dt>ML score</dt><dd>{sel.score}</dd><dt>Total in</dt><dd>{inr(sel.in_amount)}</dd><dt>Total out</dt><dd>{inr(sel.out_amount)}</dd>{sel.status && <><dt>Status</dt><dd>{sel.status}</dd></>}</dl>
            <button style={{ marginTop: 12, width: "100%" }} onClick={() => nav(`/institution/accounts/${dsId}/${sel.id}`)}>Open account trail →</button>
          </>) : <p className="muted">👆 Click any node to inspect it. Hover to highlight its flows.</p>}
        </div>
      </div>
    </div>
  );
}
