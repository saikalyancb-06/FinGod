import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { inst } from "../api.js";
import { Skeleton } from "../ui.jsx";
import { RULE_LABEL } from "./Overview.jsx";

export default function Alerts({ dsId }) {
  const [rows, setRows] = useState(null);
  const [f, setF] = useState({ q: "", priority: "", status: "", rule: "" });
  const nav = useNavigate();
  const load = () => {
    const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    inst.get(`/datasets/${dsId}/alerts?${p}`).then(setRows);
  };
  useEffect(() => { if (dsId) load(); }, [dsId, f.priority, f.status, f.rule]); // eslint-disable-line
  if (!dsId) return <div className="card">Upload a dataset on the <Link to="/institution">Overview</Link> first.</div>;
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Alerts 🚨</h1><p className="muted">Each alert is a reason to review evidence, not proof of wrongdoing. No automatic action is ever taken.</p></div></div>
      <div className="card row reveal">
        <input placeholder="🔎 Account alias (e.g. ACC-00042)" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} onKeyDown={(e) => e.key === "Enter" && load()} />
        <select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option value="">All priorities</option><option>high</option><option>medium</option><option>low</option></select>
        <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">All statuses</option>{["open", "in_review", "escalated", "closed_no_concern", "closed_reported"].map((s) => <option key={s}>{s}</option>)}</select>
        <select value={f.rule} onChange={(e) => setF({ ...f, rule: e.target.value })}><option value="">All patterns</option>{Object.entries(RULE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <button className="subtle" onClick={load}>Search</button>
        {rows && <span className="pill">{rows.length} alerts</span>}
      </div>
      <div className="prio-legend reveal small">
        <span><span className="pill high">high</span> model <b>and</b> ≥ 2 patterns agree</span>
        <span><span className="pill medium">medium</span> only one side: the model alone, <i>or</i> ≥ 2 patterns</span>
        <span><span className="pill low">low</span> 1 pattern + a moderately high model score</span>
        <span className="muted">A score of 1.00 with no pattern stays <b>medium</b>: nothing in the transactions confirms it yet.</span>
      </div>
      {!rows ? <Skeleton /> : (
        <div className="card reveal" style={{ padding: 0 }}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Priority</th><th>Account</th><th>Patterns</th><th className="r">ML score</th><th>Linked</th><th>Status</th><th>Lead evidence</th></tr></thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} style={{ cursor: "pointer" }} onClick={() => nav(`/institution/alerts/${a.id}`)}>
                    <td><span className={`pill ${a.priority}`}>{a.priority}</span>{a.priority_reason && <div className="small muted" style={{ marginTop: 4, maxWidth: 150 }}>{a.priority_reason}</div>}</td>
                    <td className="num"><b>{a.account}</b></td>
                    <td>{a.rule_families.map((r) => <span key={r} className="pill blue" style={{ marginRight: 4 }}>{RULE_LABEL[r] || r}</span>)}{!a.rule_families.length && <span className="muted small">model only</span>}</td>
                    <td className="r" title={a.model_reliable ? "" : "Not used: too little data for the model's relative features"}>
                      {a.model_reliable ? (<>
                        <div className="num"><b>{a.model_score.toFixed(2)}</b></div>
                        <div className="progress" style={{ height: 5, width: 70, marginLeft: "auto" }}><i style={{ width: `${a.model_score * 100}%`, background: a.model_score > 0.66 ? "#ef476f" : "#ffb020" }} /></div>
                      </>) : <span className="small muted">not used<br />(too little data)</span>}
                    </td>
                    <td className="num">{a.linked_alerts.length || ""}</td>
                    <td><span className={`pill ${a.status.startsWith("closed") ? "green" : a.status === "escalated" ? "amber" : ""}`}>{a.status}</span></td>
                    <td className="small muted" style={{ maxWidth: 380 }}>{a.explanation[0]}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
