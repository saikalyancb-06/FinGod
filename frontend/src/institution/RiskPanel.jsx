import { useState } from "react";
import { Link } from "react-router-dom";
import { inst, inr } from "../api.js";

const COMP = { ml: ["ML (calibrated)", "#1e5aa8"], account: ["Account behaviour", "#2e8b57"], network: ["Network position", "#b8860b"], transaction: ["Transactions", "#7a4fa3"] };
const LABEL_CLS = { "High risk / high confidence": "red", "High risk / low confidence": "amber", "Moderate risk": "blue", "Low risk": "green", "Insufficient evidence": "" };
export const labelPill = (l) => <span className={`pill ${LABEL_CLS[l] ?? ""}`}>{l}</span>;

export function ContribBar({ c, height = 10 }) {
  return (
    <div className="contrib" style={{ height }}>{Object.entries(c || {}).map(([k, v]) => <i key={k} title={`${COMP[k]?.[0]}: ${v}`} style={{ width: `${v}%`, background: COMP[k]?.[1] }} />)}</div>
  );
}

export function RiskPanel({ risk }) {
  if (!risk) return <div className="card muted small">No risk profile yet. Re-analyse the dataset to compute it.</div>;
  const u = risk.uncertainty;
  return (
    <div className="grid g2">
      <div className="card">
        <div className="row between"><h3 style={{ margin: 0 }}>Composite risk {risk.composite}/100</h3>{labelPill(risk.label)}</div>
        <ContribBar c={risk.contributions} height={14} />
        <table className="small" style={{ marginTop: 8 }}><thead><tr><th>Component</th><th className="r">Score (0–1)</th><th className="r">Points</th></tr></thead>
          <tbody>{Object.entries(COMP).map(([k, [name, col]]) => (
            <tr key={k}><td><span className="dot" style={{ background: col }} />{name}</td><td className="r num">{risk.components[k] ?? "not used"}</td><td className="r num">{risk.contributions[k] ?? "—"}</td></tr>))}</tbody></table>
        {Object.keys(risk.account_factors || {}).length > 0 && <div className="small" style={{ marginTop: 6 }}><b>Account factors:</b> {Object.entries(risk.account_factors).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>}
        {Object.keys(risk.network_factors || {}).length > 0 && <div className="small"><b>Network factors:</b> {Object.entries(risk.network_factors).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>}
        <div className="small muted" style={{ marginTop: 6 }}>Betweenness {risk.network.betweenness} · community #{risk.network.community} · degree {risk.network.degree} · ₹ through account {inr(risk.exposure)}</div>
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Confidence & uncertainty</h3>
        <dl className="kv small">
          <dt>Calibrated probability</dt><dd>{u.probability != null ? `${(u.probability * 100).toFixed(1)}%` : "not available (model not used)"}</dd>
          <dt>Raw model score</dt><dd>{u.raw_model_score}</dd>
          <dt>Conformal set (90%)</dt><dd>{u.conformal_set == null ? "—" : u.conformal_set.length === 1 ? `{${u.conformal_set[0]}}` : u.conformal_set.length ? "{mule, normal}: ambiguous" : "{ }: no label at 90%"}</dd>
          <dt>Out of distribution</dt><dd>{u.out_of_distribution == null ? "—" : u.out_of_distribution ? "yes: unlike training accounts" : "no"}</dd>
          <dt>Data sufficiency</dt><dd>{u.data_sufficiency} ({u.transactions} transactions)</dd>
          <dt>Evidence strength</dt><dd>{u.evidence_strength}</dd>
          <dt>ML and rules agree</dt><dd>{u.ml_rules_agree ? "yes" : "no"}</dd>
          <dt><b>Confidence</b></dt><dd><b>{u.confidence}</b></dd>
        </dl>
        <div className="small"><b>Investigation priority {risk.priority}/100</b></div>
        <div className="small muted">{Object.entries(risk.priority_parts).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
        <div className="small" style={{ marginTop: 6 }}><b>Missing information</b><ul style={{ margin: "2px 0", paddingLeft: 18 }}>{risk.missing_information.map((m) => <li key={m}>{m}</li>)}</ul></div>
      </div>
    </div>
  );
}

export function TracePanel({ ds, account }) {
  const [f, setF] = useState({ direction: "down", hops: 3, start: "", end: "", min_amount: 0 });
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const run = async () => {
    setErr("");
    const p = new URLSearchParams({ account, direction: f.direction, hops: f.hops, min_amount: f.min_amount || 0 });
    if (f.start) p.set("start", f.start);
    if (f.end) p.set("end", f.end);
    try { setD(await inst.get(`/datasets/${ds}/trace?${p}`)); } catch (e) { setErr(e.message); }
  };
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Trace funds from {account}</h3>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })} style={{ width: 150 }}><option value="down">Downstream (out)</option><option value="up">Upstream (in)</option><option value="both">Both</option></select>
        <label className="small">Hops <input type="number" min="1" max="6" value={f.hops} onChange={(e) => setF({ ...f, hops: +e.target.value })} style={{ width: 60 }} /></label>
        <label className="small">From <input type="datetime-local" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} /></label>
        <label className="small">To <input type="datetime-local" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} /></label>
        <label className="small">Min ₹ <input type="number" min="0" value={f.min_amount} onChange={(e) => setF({ ...f, min_amount: +e.target.value })} style={{ width: 100 }} /></label>
        <button className="small" onClick={run}>Trace</button>
      </div>
      {err && <div className="error small">{err}</div>}
      {d && (<>
        <p className="small muted">{d.accounts} accounts reached · {d.edges.length} transfers · {inr(d.total_amount)}{d.truncated ? " · truncated at 400 transfers" : ""}</p>
        {d.layering_chains.length > 0 && <div className="notice small"><b>Possible layering</b> (≥3 hops, each forwarding ≥80% within 24h):
          {d.layering_chains.map((c, i) => <div key={i}>{c.path.join(" → ")} · {c.amounts.map((x) => inr(x)).join(" → ")} · {Math.round(c.retained_share * 100)}% retained</div>)}</div>}
        <div className="table-wrap" style={{ maxHeight: 320 }}>
          <table className="small"><thead><tr><th>Hop</th><th>Time</th><th>From</th><th>To</th><th className="r">Amount</th><th>Counterparty risk</th><th>Txn</th></tr></thead>
            <tbody>{d.edges.map((e) => {
              const other = e.dir === "out" ? e.to : e.from;
              const r = d.risk[other] || {};
              return (<tr key={e.txn_id}><td>{e.hop}</td><td className="num">{e.ts.slice(0, 16)}</td><td>{e.from}</td><td>{e.to}</td><td className="r num">{inr(e.amount)}</td>
                <td>{r.label ? <Link to={`/institution/accounts/${ds}/${other}`}>{r.composite} · {r.label}</Link> : "—"}</td><td className="muted">{e.txn_id}</td></tr>);
            })}</tbody></table>
        </div>
        <p className="small muted">{d.disclaimer}</p>
      </>)}
    </div>
  );
}
