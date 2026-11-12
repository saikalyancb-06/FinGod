import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inst, inr, kfmt } from "../api.js";
import { CountUp, Stat } from "../ui.jsx";
import LiveFeed from "./LiveFeed.jsx";

const RULE_LABEL = { fan_in: "Fan-in", fan_out: "Fan-out", rapid_pass_through: "Rapid pass-through", circular_flow: "Circular flow",
  amount_splitting: "Amount splitting", dormant_activation: "Dormant activation", velocity_spike: "Velocity spike",
  shell_chain: "Layered shell chain", smurfing_fan_in: "Smurfing (in)", smurfing_fan_out: "Smurfing (out)" };
const RULE_COLOR = ["#ef476f", "#ff6b4a", "#ffb020", "#1e9bf0", "#0fb5ae", "#00a676", "#7cc243", "#ff9233", "#0c2340", "#8a9ba0"];
const RING_LABEL = { cycle: "🔄 Cycle", shell_chain: "🪆 Shell chain", smurfing_fan_in: "🐜 Smurfing in", smurfing_fan_out: "🐜 Smurfing out", fan_in_fan_out: "🌀 Collect & disperse" };
export { RULE_LABEL };

export default function Overview({ dsId, setDsId, me }) {
  const [list, setList] = useState(null);
  const [ov, setOv] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);
  const [evalRes, setEvalRes] = useState(null);
  const [tick, setTick] = useState(0);
  const [rings, setRings] = useState([]);
  const fileRef = useRef();
  const labRef = useRef();
  const loadList = () => inst.get("/datasets").then((l) => { setList(l); if (!dsId && l.length) setDsId(l[0].id); });
  useEffect(() => { loadList(); }, []); // eslint-disable-line
  useEffect(() => { if (dsId) inst.get(`/datasets/${dsId}/overview`).then(setOv).catch(() => setOv(null)); }, [dsId, result, evalRes, tick]);
  useEffect(() => { if (dsId) inst.get(`/datasets/${dsId}/rings`).then(setRings).catch(() => setRings([])); }, [dsId, result, tick]);
  const exportJson = async () => {
    const j = await inst.get(`/datasets/${dsId}/export.json`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(j, null, 2)], { type: "application/json" }));
    Object.assign(document.createElement("a"), { href: url, download: `muling-analysis-dataset-${dsId}.json` }).click();
  };
  const upload = async (blob, name) => {
    setErr(""); setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", blob, name); fd.append("name", name);
      const r = await inst.post("/datasets", fd);
      setResult(r); setDsId(r.dataset_id); loadList();
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  const sample = async () => upload(new Blob([await (await fetch("/x-inst/api/sample-dataset", { headers: { Authorization: `Bearer ${localStorage.getItem("institution_token")}` } })).text()], { type: "text/csv" }), "SYNTHETIC_mule_demo_transactions.csv");
  const s = ov?.summary;
  const rules = s ? Object.entries(s.rule_hits || {}).map(([k, v]) => ({ k: RULE_LABEL[k] || k, v })) : [];
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Institutional overview 📊</h1><p className="muted">Upload authorised multi-account records. Accounts are shown by alias; real identifiers are restricted.</p></div>
        {list?.length > 0 && <select value={dsId || ""} onChange={(e) => setDsId(+e.target.value)}>{list.map((d) => <option key={d.id} value={d.id}>#{d.id} · {d.name}</option>)}</select>}
      </div>

      <div className="card hero reveal">
        <h3 style={{ color: "#fff" }}>⬆️ Analyse a transaction file</h3>
        <p style={{ margin: "0 0 12px" }}>CSV with timestamp, from/sender, to/receiver, amount (optional txn_id, channel). The pipeline normalises, builds the account graph, runs rules + ML + graph analytics, and raises evidence-backed alerts.</p>
        <div className="row" style={{ position: "relative", zIndex: 1 }}>
          <input ref={fileRef} type="file" accept=".csv" hidden onChange={(e) => e.target.files[0] && upload(e.target.files[0], e.target.files[0].name)} />
          <button style={{ background: "#fff", color: "var(--navy)" }} disabled={busy} onClick={() => fileRef.current.click()}>{busy ? "Analysing network…" : "Choose CSV"}</button>
          <button style={{ background: "var(--warm-grad)" }} disabled={busy} onClick={sample}>✨ Use synthetic demo dataset</button>
        </div>
      </div>
      {err && <div className="error">{err}</div>}
      <LiveFeed dsId={dsId} setDsId={(id) => { setDsId(id); loadList(); }} onBatch={() => setTick((t) => t + 1)} />

      {s && (<>
        <div className="grid g4 stagger">
          <Stat color="sky" icon="👥" label="Accounts analysed" value={s.accounts} format={(v) => Math.round(v).toLocaleString("en-IN")} />
          <Stat color="emerald" icon="🔀" label="Transactions" value={s.transactions} format={(v) => Math.round(v).toLocaleString("en-IN")} sub={`${ov.dataset.report.rows_dropped_invalid} invalid, ${ov.dataset.report.rows_dropped_duplicate} duplicate rows dropped`} />
          <Stat color="coral" icon="🚨" label="Alerts raised" value={s.alerts} format={(v) => Math.round(v)} sub={Object.entries(s.by_priority || {}).map(([k, v]) => `${v} ${k}`).join(" · ")} />
          <Stat color="saffron" icon="🕵️" label="Open for review" value={(ov.status_counts.open || 0) + (ov.status_counts.in_review || 0) + (ov.status_counts.escalated || 0)} format={(v) => Math.round(v)} sub={Object.entries(ov.status_counts).map(([k, v]) => `${k}: ${v}`).join(" · ")} />
        </div>
        <div className="grid g2 stagger">
          <div className="card lift">
            <h3>📈 Daily transaction volume</h3>
            <div style={{ height: 230 }}>
              <ResponsiveContainer>
                <AreaChart data={ov.daily_volume}>
                  <defs><linearGradient id="iv" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#1e9bf0" stopOpacity={0.45} /><stop offset="100%" stopColor="#0fb5ae" stopOpacity={0.05} /></linearGradient></defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#dfe8f1" /><XAxis dataKey="date" hide /><YAxis tickFormatter={kfmt} />
                  <Tooltip formatter={(v, n) => (n === "amount" ? inr(v) : v)} />
                  <Area dataKey="amount" stroke="#1e9bf0" strokeWidth={2} fill="url(#iv)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="card lift">
            <h3>🧩 Rule families behind alerts</h3>
            <div style={{ height: 230 }}>
              <ResponsiveContainer>
                <BarChart data={rules} layout="vertical" margin={{ left: 30 }}>
                  <XAxis type="number" hide /><YAxis type="category" dataKey="k" width={130} tick={{ fontSize: 12 }} /><Tooltip />
                  <Bar dataKey="v" radius={[0, 8, 8, 0]}>{rules.map((r, i) => <Cell key={r.k} fill={RULE_COLOR[i % RULE_COLOR.length]} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
        <div className="grid g2 stagger">
          <div className="card">
            <h3>⚙️ Detection layers</h3>
            <div className="grid g3" style={{ gap: 10 }}>
              <div className="card tint" style={{ padding: 12 }}><b>1 · Rules</b><div className="small muted">fan-in/out, pass-through, cycles, splitting, dormancy, velocity</div></div>
              <div className="card tint" style={{ padding: 12 }}><b>2 · ML</b><div className="small muted">{s.model_version}<br />threshold {s.threshold?.toFixed(2)}</div></div>
              <div className="card tint" style={{ padding: 12 }}><b>3 · Graph</b><div className="small muted">PageRank, clustering, temporal cycles, linked alerts</div></div>
            </div>
            <p className="small muted">Scores aren't averaged. Priority comes from explicit rules (see Model &amp; validation).</p>
            <div className="row"><Link className="btn" to="/institution/alerts">Review alerts →</Link><button className="subtle" onClick={exportJson}>⬇️ Export JSON</button></div>
          </div>
          <div className="card">
            <h3>🧪 Measure against known labels (synthetic tests)</h3>
            <p className="small muted" style={{ marginTop: 0 }}>Upload a labels CSV (account, is_mule) for a synthetic file to report precision, recall and false-positive rate. The demo labels live in <code>data/samples/mule_demo_labels_for_evaluation.csv</code>.</p>
            <input ref={labRef} type="file" accept=".csv" hidden onChange={async (e) => { const fd = new FormData(); fd.append("file", e.target.files[0]); setEvalRes(await inst.post(`/datasets/${dsId}/evaluate`, fd)); }} />
            <button className="subtle" onClick={() => labRef.current.click()}>Upload labels CSV</button>
            {(evalRes || s.evaluation) && (() => { const ev = evalRes || s.evaluation; return (
              <table style={{ marginTop: 10 }}><thead><tr><th>Alerts counted</th><th className="r">Precision</th><th className="r">Recall</th><th className="r">FPR</th><th className="r">FP</th></tr></thead>
                <tbody>{["high_only", "medium_or_high", "any_alert"].map((k) => <tr key={k}><td>{k.replaceAll("_", " ")}</td><td className="r num">{ev[k].precision}</td><td className="r num">{ev[k].recall}</td><td className="r num">{ev[k].false_positive_rate}</td><td className="r num">{ev[k].fp}</td></tr>)}</tbody>
              </table>); })()}
          </div>
        </div>
      </>)}
      {s && rings.length > 0 && (
        <div className="card reveal">
          <div className="row between"><h3><span className="chip-ico" style={{ background: "var(--rose-bg)" }}>💍</span>Fraud ring candidates ({rings.length})</h3>
            <span className="small muted">Ring risk = pattern base + 2 × members (max 100), from MuleGuard</span></div>
          <div className="table-wrap" style={{ maxHeight: 340 }}>
            <table><thead><tr><th>Ring</th><th>Pattern</th><th className="r">Members</th><th className="r">Flagged</th><th>Risk</th><th>Accounts</th></tr></thead>
              <tbody>{rings.map((r) => (
                <tr key={r.ring_id}><td className="num"><b>{r.ring_id}</b></td><td>{RING_LABEL[r.pattern_type] || r.pattern_type}</td>
                  <td className="r num">{r.members}</td><td className="r num">{r.flagged_members.length}</td>
                  <td style={{ minWidth: 120 }}><div className="row" style={{ gap: 6 }}><b className="num">{r.risk_score}</b>
                    <div className="progress" style={{ height: 6, flex: 1 }}><i style={{ width: `${r.risk_score}%`, background: r.risk_score >= 95 ? "#ef476f" : r.risk_score >= 88 ? "#ff9233" : "#ffb020" }} /></div></div></td>
                  <td className="small">{r.member_accounts.slice(0, 8).map((m) => <Link key={m} to={`/institution/accounts/${dsId}/${m}`} className={`pill ${r.flagged_members.includes(m) ? "red" : ""}`} style={{ marginRight: 3, textDecoration: "none" }}>{m}</Link>)}{r.members > 8 && <span className="muted"> +{r.members - 8}</span>}</td></tr>))}
              </tbody></table>
          </div>
          <p className="small muted" style={{ marginBottom: 0 }}>Smurfing groups include the counterparties, who may be victims rather than participants. Red = account with its own alert.</p>
        </div>
      )}
      {list?.length === 0 && !busy && <div className="card muted">No datasets yet. Upload a file or use the synthetic demo dataset.</div>}
    </div>
  );
}
