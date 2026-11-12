import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { inst, inr } from "../api.js";
import { Skeleton, Stat } from "../ui.jsx";
import Graph, { GraphLegend } from "./Graph.jsx";
import { Timeline } from "./AlertDetail.jsx";

export default function AccountView() {
  const { ds, alias } = useParams();
  const nav = useNavigate();
  const [a, setA] = useState(null);
  const [g, setG] = useState(null);
  useEffect(() => {
    setA(null); setG(null);
    inst.get(`/datasets/${ds}/accounts/${alias}`).then(setA);
    inst.get(`/datasets/${ds}/graph?account=${alias}&hops=2&limit=80`).then(setG);
  }, [ds, alias]);
  if (!a) return <Skeleton rows={3} />;
  const cp = (rows, color) => rows.slice(0, 8).map((c) => (
    <div key={c.account} className="row between" style={{ padding: "6px 0", borderBottom: "1px dashed var(--line)" }}>
      <Link to={`/institution/accounts/${ds}/${c.account}`} className="num">{c.account}</Link>
      <span className="num"><b style={{ color }}>{inr(c.total)}</b> <span className="muted small">{c.count} txns</span></span>
    </div>));
  return (
    <div className="stack">
      <div className="page-head reveal">
        <div><h1>Account {alias}</h1><p className="muted">Incoming and outgoing trails, counterparties and time sequence.</p></div>
        {a.alert ? <Link className="btn warm" to={`/institution/alerts/${a.alert.id}`}>Open alert #{a.alert.id} ({a.alert.priority}) →</Link> : <span className="pill green">no alert on this account</span>}
      </div>
      <div className="grid g4 stagger">
        <Stat color="emerald" icon="📥" label="Total in" value={a.totals.in} format={inr} />
        <Stat color="coral" icon="📤" label="Total out" value={a.totals.out} format={inr} />
        <Stat color="sky" icon="👥" label="Counterparties" value={a.senders.length + a.receivers.length} format={(v) => Math.round(v)} />
        <Stat color="saffron" icon="🧠" label="ML score" value={a.model_score} format={(v) => v.toFixed(2)} />
      </div>
      <div className="card reveal"><div className="row between"><h3>🕸️ Network</h3><GraphLegend /></div>
        {g ? <Graph data={g} height={420} onSelect={(n) => !n.focus && nav(`/institution/accounts/${ds}/${n.id}`)} /> : <div className="skeleton" style={{ height: 420 }} />}</div>
      <div className="grid g3 stagger">
        <div className="card"><h3>⬅️ Top senders</h3>{cp(a.senders, "var(--emerald)")}</div>
        <div className="card"><h3>➡️ Top receivers</h3>{cp(a.receivers, "var(--coral)")}</div>
        <div className="card"><h3>⏱️ Activity</h3><Timeline tl={a.timeline} /></div>
      </div>
      <div className="card reveal" style={{ padding: 0 }}>
        <div className="table-wrap" style={{ maxHeight: 420 }}>
          <table><thead><tr><th>Time</th><th>Direction</th><th>Counterparty</th><th className="r">Amount</th><th>Channel</th></tr></thead>
            <tbody>{a.timeline.map((t) => (
              <tr key={t.txn_id}><td className="num small">{new Date(t.ts).toLocaleString("en-IN")}</td>
                <td><span className={`pill ${t.direction === "in" ? "green" : "red"}`}>{t.direction === "in" ? "⬅ in" : "out ➡"}</span></td>
                <td><Link to={`/institution/accounts/${ds}/${t.counterparty}`} className="num">{t.counterparty}</Link></td>
                <td className="r num"><b>{inr(t.amount)}</b></td><td className="small muted">{t.channel}</td></tr>))}</tbody></table>
        </div>
      </div>
    </div>
  );
}
