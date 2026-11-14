import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inst, inr } from "../api.js";
import { Skeleton } from "../ui.jsx";
import { ContribBar, labelPill } from "./RiskPanel.jsx";

const LABELS = ["High risk / high confidence", "High risk / low confidence", "Moderate risk", "Low risk", "Insufficient evidence"];

export default function RiskIntel({ dsId }) {
  const [d, setD] = useState(null);
  const [label, setLabel] = useState("");
  const [flagged, setFlagged] = useState(false);
  const [w, setW] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showDoc, setShowDoc] = useState(false);
  const load = () => {
    const p = new URLSearchParams({ limit: 300 });
    if (label) p.set("label", label);
    if (flagged) p.set("flagged_only", "true");
    inst.get(`/datasets/${dsId}/risk?${p}`).then((r) => { setD(r); setW(r.weights); });
  };
  useEffect(() => { if (dsId) load(); }, [dsId, label, flagged]); // eslint-disable-line
  if (!dsId) return <div className="card">Choose a dataset on the <Link to="/institution">Overview</Link> first.</div>;
  if (!d) return <Skeleton rows={4} />;
  const save = async () => { setBusy(true); await inst.put(`/datasets/${dsId}/risk-weights`, w); setBusy(false); load(); };
  return (
    <div className="stack">
      <div className="page-head"><div><h1>Risk intelligence</h1>
        <p className="muted">Transaction-, account- and network-level risk for every account, with uncertainty kept separate from the score.</p></div></div>
      <div className="label-grid">
        {LABELS.map((l) => <button key={l} className={`stat-btn ${label === l ? "on" : ""}`} onClick={() => setLabel(label === l ? "" : l)}>
          <div className="small muted">{l}</div><div className="num" style={{ fontSize: 22, fontWeight: 700 }}>{d.labels[l] || 0}</div></button>)}
      </div>
      <div className="grid g2">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Composite risk distribution</h3>
          <div style={{ height: 200 }}><ResponsiveContainer><BarChart data={d.histogram}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="bucket" fontSize={11} /><YAxis allowDecimals={false} fontSize={11} /><Tooltip /><Bar dataKey="accounts" fill="#1e5aa8" /></BarChart></ResponsiveContainer></div>
          <p className="small muted">Money through alerted accounts: <b>{inr(d.flagged_exposure || 0)}</b>. Uncertainty model: {d.uncertainty_model || "not trained (run python -m ml.compare_mule_models)"}.</p>
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Component weights (this dataset)</h3>
          {w && Object.keys(w).map((k) => (
            <label key={k} className="row between small" style={{ gap: 8 }}>{k}<input type="range" min="0" max="1" step="0.05" value={w[k]} onChange={(e) => setW({ ...w, [k]: +e.target.value })} style={{ flex: 1, padding: 0, border: "none" }} /><b className="num" style={{ width: 40 }}>{w[k]}</b></label>))}
          <div className="row" style={{ gap: 6 }}><button className="small" disabled={busy} onClick={save}>{busy ? "Re-scoring…" : "Apply and re-score"}</button>
            <button className="small ghost" onClick={() => setW({ ml: 0.35, account: 0.25, network: 0.25, transaction: 0.15 })}>Defaults</button>
            <button className="small ghost" onClick={() => setShowDoc(!showDoc)}>{showDoc ? "Hide" : "Show"} methodology</button></div>
          <p className="small muted">Weights are normalised over the components available for each account (ML is skipped when the model doesn't apply). Changes are audited.</p>
        </div>
      </div>
      {showDoc && <div className="card"><pre className="methodology">{d.methodology}</pre></div>}
      {d.networks.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Suspicious communities (Louvain)</h3>
          <div className="table-wrap" style={{ maxHeight: 260 }}><table className="small"><thead><tr><th>#</th><th className="r">Members</th><th className="r">Flagged</th><th className="r">Flow</th><th className="r">Density</th><th>Accounts</th></tr></thead>
            <tbody>{d.networks.map((c) => <tr key={c.id}><td>{c.id}</td><td className="r num">{c.size}</td><td className="r num">{c.flagged}</td><td className="r num">{inr(c.flow)}</td><td className="r num">{c.density}</td>
              <td>{c.members.slice(0, 8).map((m) => <Link key={m} to={`/institution/accounts/${dsId}/${m}`} className="pill" style={{ marginRight: 3 }}>{m}</Link>)}{c.size > 8 ? ` +${c.size - 8}` : ""}</td></tr>)}</tbody></table></div>
        </div>)}
      <div className="card" style={{ padding: 0 }}>
        <div className="row between" style={{ padding: "10px 14px" }}><b>{d.total} accounts{label ? ` · ${label}` : ""}</b>
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={flagged} onChange={(e) => setFlagged(e.target.checked)} style={{ width: 16 }} />Alerted accounts only</label></div>
        <div className="table-wrap" style={{ maxHeight: 560 }}><table className="small">
          <thead><tr><th>Account</th><th>Label</th><th className="r">Risk</th><th style={{ width: 160 }}>Breakdown</th><th className="r">P(mule)</th><th>Conformal</th><th className="r">Txns</th><th className="r">Priority</th><th className="r">Exposure</th></tr></thead>
          <tbody>{d.accounts.map((a) => (
            <tr key={a.account}>
              <td><Link to={a.alert_id ? `/institution/alerts/${a.alert_id}` : `/institution/accounts/${dsId}/${a.account}`}><b>{a.account}</b></Link></td>
              <td>{labelPill(a.label)}</td><td className="r num"><b>{a.composite}</b></td><td><ContribBar c={a.contributions} /></td>
              <td className="r num">{a.uncertainty.probability != null ? `${(a.uncertainty.probability * 100).toFixed(1)}%` : "—"}</td>
              <td className="small">{a.uncertainty.conformal_set == null ? "—" : a.uncertainty.conformal_set.length === 1 ? a.uncertainty.conformal_set[0] : "uncertain"}{a.uncertainty.out_of_distribution ? " · OOD" : ""}</td>
              <td className="r num">{a.uncertainty.transactions}</td><td className="r num">{a.priority}</td><td className="r num">{inr(a.exposure)}</td></tr>))}</tbody></table></div>
        <div className="legend small" style={{ padding: "8px 14px" }}>{[["ML", "#1e5aa8"], ["Account", "#2e8b57"], ["Network", "#b8860b"], ["Transactions", "#7a4fa3"]].map(([n, c]) => <span key={n}><i style={{ background: c }} />{n}</span>)}</div>
      </div>
    </div>
  );
}
