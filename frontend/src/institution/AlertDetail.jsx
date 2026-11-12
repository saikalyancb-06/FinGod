import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, Legend } from "recharts";
import { inst, inr, kfmt } from "../api.js";
import { Md, Skeleton, Typing } from "../ui.jsx";
import Graph, { GraphLegend } from "./Graph.jsx";
import { RULE_LABEL } from "./Overview.jsx";

const STATUS_FLOW = { open: "🆕", in_review: "🔍", escalated: "⬆️", closed_no_concern: "✅", closed_reported: "📨" };

export function Timeline({ tl }) {
  const pts = tl.map((t) => ({ x: new Date(t.ts).getTime(), y: t.amount, dir: t.direction, cp: t.counterparty }));
  return (
    <div style={{ height: 230 }}>
      <ResponsiveContainer>
        <ScatterChart margin={{ left: 10, right: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#dfe8f1" />
          <XAxis dataKey="x" type="number" domain={["auto", "auto"]} tickFormatter={(v) => new Date(v).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} name="time" />
          <YAxis dataKey="y" tickFormatter={kfmt} name="amount" />
          <ZAxis range={[50, 50]} />
          <Tooltip formatter={(v, n) => (n === "time" ? new Date(v).toLocaleString("en-IN") : inr(v))} />
          <Legend />
          <Scatter name="Incoming" data={pts.filter((p) => p.dir === "in")} fill="#00a676" />
          <Scatter name="Outgoing" data={pts.filter((p) => p.dir === "out")} fill="#ff6b4a" />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function AlertDetail({ me }) {
  const { id } = useParams();
  const nav = useNavigate();
  const [a, setA] = useState(null);
  const [acc, setAcc] = useState(null);
  const [g, setG] = useState(null);
  const [note, setNote] = useState("");
  const [st, setSt] = useState({ status: "", reason: "" });
  const [summary, setSummary] = useState(null);
  const [sumBusy, setSumBusy] = useState(false);
  const [revealed, setRevealed] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    inst.get(`/alerts/${id}`).then((x) => {
      setA(x);
      inst.get(`/datasets/${x.dataset_id}/accounts/${x.account}`).then(setAcc);
      inst.get(`/datasets/${x.dataset_id}/graph?account=${x.account}&hops=2&limit=90`).then(setG);
    });
  }, [id]);
  if (!a) return <Skeleton rows={3} />;
  const act = async (fn) => { setErr(""); try { setA(await fn()); } catch (e) { setErr(e.message); } };
  const download = async () => {
    const r = await fetch(`/x-inst/api/alerts/${id}/report`, { headers: { Authorization: `Bearer ${localStorage.getItem("institution_token")}` } });
    const url = URL.createObjectURL(new Blob([await r.text()], { type: "text/markdown" }));
    Object.assign(document.createElement("a"), { href: url, download: `alert_${id}_${a.account}_report.md` }).click();
  };
  return (
    <div className="stack">
      <div className="row"><Link to="/institution/alerts" className="small">← all alerts</Link></div>
      <div className="card hero reveal" style={{ background: a.priority === "high" ? "linear-gradient(135deg,#ef476f,#ff6b4a)" : a.priority === "medium" ? "linear-gradient(135deg,#ff9233,#ffb020)" : undefined }}>
        <div className="row between" style={{ position: "relative", zIndex: 1 }}>
          <div>
            <div className="small" style={{ opacity: .85, textTransform: "uppercase", fontWeight: 800, letterSpacing: ".08em" }}>Alert #{a.id} · {a.priority} priority</div>
            <h1 style={{ color: "#fff", margin: "4px 0" }}>{a.account}</h1>
            <div className="row">{a.rule_families.map((r) => <span key={r} className="pill" style={{ background: "rgba(255,255,255,.25)", color: "#fff" }}>{RULE_LABEL[r] || r}</span>)}</div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div className="small" style={{ opacity: .85 }}>ML score / threshold</div>
            <div className="num" style={{ fontSize: 34, fontWeight: 700 }}>{a.model_score.toFixed(2)} <span style={{ fontSize: 16, opacity: .8 }}>/ {a.threshold?.toFixed(2)}</span></div>
            <div className="pill" style={{ background: "#fff", color: "var(--ink)" }}>{STATUS_FLOW[a.status]} {a.status}</div>
          </div>
        </div>
      </div>
      <div className="notice small">⚖️ {a.disclaimer}</div>
      {err && <div className="error">{err}</div>}

      <div className="grid g2 stagger">
        <div className="card">
          <h3>🧾 Evidence</h3>
          <ol style={{ paddingLeft: 20, margin: 0 }}>{a.explanation.map((e) => <li key={e} style={{ marginBottom: 6 }}>{e}</li>)}</ol>
          {a.linked_alerts.length > 0 && <div style={{ marginTop: 10 }}><span className="small muted">Linked alerted accounts (≤2 hops): </span>
            {a.linked_alerts.slice(0, 12).map((x) => <Link key={x} to={`/institution/accounts/${a.dataset_id}/${x}`} className="pill blue" style={{ marginRight: 4, textDecoration: "none" }}>{x}</Link>)}</div>}
          <div className="row" style={{ marginTop: 10 }}>
            <span className="pill amber" title="MuleGuard-style pattern score: 40 cycle, 35 shell chain, 25 smurfing, +5 per extra pattern">Pattern score {a.pattern_score ?? 0}/100</span>
            {(a.ring_ids || []).map((r) => <span key={r} className="pill red">{r}</span>)}
          </div>
          <div className="divider" />
          <div className="row"><button className="warm small" disabled={sumBusy} onClick={async () => { setSumBusy(true); setSummary(await inst.post(`/alerts/${id}/summary`)); setSumBusy(false); }}>✨ AI evidence summary</button>
            <button className="small subtle" onClick={download}>⬇️ Export report</button>
            <Link className="btn small ghost" to={`/institution/accounts/${a.dataset_id}/${a.account}`}>Full account trail →</Link></div>
          {sumBusy && <Typing />}
          {summary && <div className="notice info" style={{ marginTop: 10 }}><Md text={summary.summary} /><div className="small muted" style={{ marginTop: 6 }}>{summary.llm_used ? "Generated by AI from the evidence above only." : "Template summary."}</div></div>}
        </div>
        <div className="card">
          <h3>🔢 Key indicators</h3>
          <dl className="kv small">
            {[["Incoming transfers", a.features.in_count], ["Distinct senders", a.features.in_degree], ["Outgoing transfers", a.features.out_count], ["Distinct receivers", a.features.out_degree],
              ["Total in", inr(a.features.in_amount)], ["Total out", inr(a.features.out_amount)], ["Forwarded within 60 min", `${Math.round(a.features.pass_through_ratio * 100)}%`],
              ["Median holding time", a.features.median_hold_minutes >= 1e5 ? "—" : `${Math.round(a.features.median_hold_minutes)} min`], ["Max senders in 24h", a.features.max_senders_24h],
              ["Max receivers in 24h", a.features.max_receivers_24h], ["In a temporal cycle", a.features.in_temporal_cycle ? "yes" : "no"], ["Active days", a.features.active_days]].map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
          </dl>
          {me.role === "lead" && (
            <div style={{ marginTop: 12 }}>
              {revealed ? <div className="notice info">🔓 Real identifier: <b className="num">{revealed}</b> (access logged)</div> :
                <button className="small subtle" onClick={async () => { const reason = prompt("Reason for revealing the real account identifier (logged):"); if (reason) setRevealed((await inst.post(`/datasets/${a.dataset_id}/accounts/${a.account}/reveal`, { text: reason })).account_identifier); }}>🔐 Reveal real identifier</button>}
            </div>)}
        </div>
      </div>

      <div className="card reveal">
        <div className="row between"><h3>🕸️ Connected accounts (2 hops)</h3><GraphLegend /></div>
        {g ? <Graph data={g} height={460} onSelect={(n) => !n.focus && nav(`/institution/accounts/${a.dataset_id}/${n.id}`)} /> : <div className="skeleton" style={{ height: 460 }} />}
        <p className="small muted">Hover to highlight flows. Click a node to open that account.</p>
      </div>

      {acc && (
        <div className="grid g2 stagger">
          <div className="card"><h3>⏱️ Time-ordered activity</h3><Timeline tl={acc.timeline} /></div>
          <div className="card">
            <h3>💬 Case notes &amp; human review</h3>
            <div className="stack" style={{ maxHeight: 200, overflow: "auto" }}>
              {a.notes.length === 0 && <span className="muted small">No notes yet.</span>}
              {a.notes.map((n, i) => <div key={i} className="small"><b>{n.author}</b> <span className="muted">{new Date(n.created_at).toLocaleString("en-IN")}</span><div>{n.text}</div></div>)}
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <input style={{ flex: 1 }} placeholder="Add an investigation note" value={note} onChange={(e) => setNote(e.target.value)} />
              <button className="small" disabled={!note} onClick={() => act(async () => { const r = await inst.post(`/alerts/${id}/notes`, { text: note }); setNote(""); return r; })}>Add</button>
            </div>
            <div className="divider" />
            <div className="row">
              <select value={st.status} onChange={(e) => setSt({ ...st, status: e.target.value })}>
                <option value="">Change status…</option><option value="in_review">in_review</option><option value="escalated">escalated</option>
                <option value="closed_no_concern" disabled={me.role !== "lead"}>closed_no_concern {me.role !== "lead" ? "(lead)" : ""}</option>
                <option value="closed_reported" disabled={me.role !== "lead"}>closed_reported {me.role !== "lead" ? "(lead)" : ""}</option>
              </select>
              <input style={{ flex: 1 }} placeholder="Reason (required)" value={st.reason} onChange={(e) => setSt({ ...st, reason: e.target.value })} />
              <button className="small warm" disabled={!st.status || st.reason.length < 3} onClick={() => act(async () => { const r = await inst.post(`/alerts/${id}/status`, st); setSt({ status: "", reason: "" }); return r; })}>Record</button>
            </div>
            <p className="small muted">Final dispositions are recorded by a lead investigator. The system never freezes accounts.</p>
          </div>
        </div>
      )}
    </div>
  );
}
