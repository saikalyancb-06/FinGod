import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { inst, inr } from "../api.js";
import { useLive } from "./LiveContext.jsx";

const PATTERN_ICON = { fan_in_out: "🌀", pass_through: "⛓️", cycle: "🔄", smurfing: "🐜", structuring: "🧱" };

function Ticker({ rows }) {
  return (
    <div className="ticker">
      {rows.length === 0 && <div className="small muted" style={{ padding: 10 }}>Waiting for transactions…</div>}
      {rows.slice(0, 14).map((r) => (
        <div key={r.txn_id} className="ticker-row">
          <span className="num small muted">{r.ts.slice(5, 16)}</span>
          <span className="num"><b>{r.src}</b> → <b>{r.dst}</b></span>
          <span className="num" style={{ marginLeft: "auto", fontWeight: 700 }}>{inr(r.amount)}</span>
        </div>))}
    </div>
  );
}

function External({ setDsId }) {
  // mr-mule / MuleGuard live mode: the browser listens to an external WebSocket and forwards rows for analysis.
  const live = useLive();
  const [url, setUrl] = useState("ws://localhost:3000");
  const [state, setState] = useState("idle");
  const [count, setCount] = useState(0);
  const buf = useRef([]);
  const sock = useRef(null);
  const target = useRef(null);
  useEffect(() => {
    const t = setInterval(async () => {
      if (!buf.current.length || !target.current) return;
      const rows = buf.current.splice(0, 2000);
      try { await inst.post(`/datasets/${target.current}/append`, { rows }); } catch (e) { live.toast({ kind: "info", title: "Feed error", body: e.message }); }
    }, 2000);
    return () => { clearInterval(t); sock.current?.close(); };
  }, []); // eslint-disable-line
  const connect = async () => {
    const r = await inst.post("/datasets/live", { name: `External feed ${new Date().toLocaleTimeString("en-IN")}` });
    target.current = r.dataset_id; setDsId(r.dataset_id); live.watch(r.dataset_id); setCount(0);
    const s = new WebSocket(url);
    sock.current = s; setState("connecting");
    s.onopen = () => setState("connected");
    s.onclose = () => setState("disconnected");
    s.onerror = () => setState("error: is the feed running?");
    s.onmessage = (ev) => {
      let row = ev.data;
      try { const j = JSON.parse(ev.data); if (j.type && j.type !== "csv") return; row = j.row ?? row; } catch { /* raw CSV */ }
      String(row).split(/\r?\n/).filter(Boolean).forEach((x) => { buf.current.push(x); setCount((c) => c + 1); });
    };
  };
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>Listen to an external WebSocket that emits <code>transaction_id,sender_id,receiver_id,amount,timestamp</code> rows, such as the team's <b>mr-mule</b> app
        (run <code>npm install &amp;&amp; npm start</code> in <code>reference/mr-mule</code>, then open <code>localhost:3000</code> to send transactions).</p>
      <div className="row">
        <input style={{ flex: 1, minWidth: 200 }} value={url} onChange={(e) => setUrl(e.target.value)} />
        {state === "connected" ? <button className="danger small" onClick={() => sock.current?.close()}>Disconnect</button>
          : <button className="small" onClick={connect}>Connect</button>}
        <span className="pill">{state} · {count} rows</span>
      </div>
    </div>
  );
}

function Manual({ ensureDataset }) {
  const live = useLive();
  const [f, setF] = useState({ sender: "AC1000001", receiver: "AC2000002", amount: 25000 });
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState(null);
  const send = async (e) => {
    e.preventDefault(); setBusy(true);
    try { await ensureDataset(); setLast(await live.manual({ ...f, amount: +f.amount })); } catch (x) { live.toast({ kind: "info", title: "Could not send", body: x.message }); } finally { setBusy(false); }
  };
  const quick = (amount) => setF({ ...f, sender: `AC${Math.floor(1e6 + Math.random() * 9e6)}`, amount });
  return (
    <form className="stack" onSubmit={send}>
      <p className="small muted" style={{ margin: 0 }}>Type transactions yourself (like mr-mule). Try sending 10 payments from different senders to the same receiver to watch a fan-in alert appear.</p>
      <div className="grid g3" style={{ gap: 10 }}>
        <label className="field">Sender<input value={f.sender} onChange={(e) => setF({ ...f, sender: e.target.value })} /></label>
        <label className="field">Receiver<input value={f.receiver} onChange={(e) => setF({ ...f, receiver: e.target.value })} /></label>
        <label className="field">Amount ₹<input type="number" min="1" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></label>
      </div>
      <div className="row">
        <button disabled={busy}>{busy ? "Analysing…" : "Send transaction"}</button>
        <button type="button" className="subtle small" onClick={() => quick(12000)}>🎲 New random sender</button>
        {last && <span className="small muted">Added {last.added} · {last.summary?.alerts ?? 0} alerts in this stream</span>}
      </div>
    </form>
  );
}

export default function LiveFeed({ setDsId }) {
  const live = useLive();
  const [tab, setTab] = useState("sim");
  const [speed, setSpeed] = useState(1.5);
  const [auto, setAuto] = useState(true);
  const [busy, setBusy] = useState("");
  const begin = async () => { setBusy("start"); try { const id = await live.start({ speed, auto_patterns: auto }); setDsId(id); } finally { setBusy(""); } };
  const ensureDataset = async () => {
    if (live.dsId) return live.dsId;
    const id = await live.start({ speed, auto_patterns: false });
    setDsId(id);
    return id;
  };
  const s = live.summary;
  return (
    <div className="card stack">
      <div className="row between">
        <h3 style={{ margin: 0 }}><span className="chip-ico" style={{ background: "var(--coral-bg)" }}>📡</span>Live transaction monitoring</h3>
        <div className="row" style={{ gap: 6 }}>
          <span className={`pill ${live.running ? "red" : ""}`}>{live.running ? "● LIVE" : "idle"}</span>
          <span className={`pill ${live.connected ? "green" : ""}`}>{live.connected ? "socket connected" : "socket closed"}</span>
          {live.dsId && <span className="pill">dataset #{live.dsId}</span>}
        </div>
      </div>
      <div className="tabs" style={{ maxWidth: 560 }}>
        {[["sim", "🧪 Built-in simulator"], ["manual", "✍️ Manual transactions"], ["ext", "🔌 External feed (mr-mule)"]].map(([k, l]) => (
          <button key={k} type="button" className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>))}
      </div>

      {tab === "sim" && (
        <div className="stack">
          <p className="small muted" style={{ margin: 0 }}>Streams realistic everyday payments (salaries, UPI to shops, friends) on a fast clock (1 tick ≈ 1 simulated hour, after loading 90 days of normal account history) and injects money-mule patterns. Every batch is analysed by rules + the IBM-trained model + graph analytics, and new alerts pop up instantly.</p>
          {!live.running ? (
            <div className="row">
              <label className="field" style={{ minWidth: 180 }}>Speed: {speed}×<input type="range" min="0.5" max="4" step="0.5" value={speed} onChange={(e) => setSpeed(+e.target.value)} style={{ padding: 0, border: "none" }} /></label>
              <label className="row small"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> inject patterns automatically</label>
              <button className="warm" disabled={!!busy} onClick={begin}>▶ Start live stream</button>
            </div>
          ) : (
            <div className="stack">
              <div className="row">
                <span className="small muted">Inject a pattern now:</span>
                {Object.entries(live.patterns || {}).map(([k, label]) => (
                  <button key={k} className="small subtle" title={label} onClick={() => live.inject(k)}>{PATTERN_ICON[k]} {k.replaceAll("_", " ")}</button>))}
                <button className="small danger" onClick={live.stop}>■ Stop</button>
              </div>
            </div>
          )}
        </div>
      )}
      {tab === "manual" && <Manual ensureDataset={ensureDataset} />}
      {tab === "ext" && <External setDsId={setDsId} />}

      {live.dsId && (
        <div className="grid g3" style={{ gap: 12 }}>
          <div className="card tint" style={{ padding: 12, boxShadow: "none" }}>
            <div className="small muted">Streamed</div><div className="num" style={{ fontSize: 24, fontWeight: 700 }}>{live.sent.toLocaleString("en-IN")}</div>
            <div className="small muted">{s ? `${s.accounts} accounts · ${s.alerts} alerts · ${s.fraud_rings ?? 0} rings` : "analysing…"}</div>
            {live.clock && <div className="small muted">sim clock {live.clock.slice(5, 16)}</div>}
          </div>
          <div style={{ gridColumn: "span 2", minWidth: 0 }}>
            <div className="small muted" style={{ marginBottom: 4 }}>Latest transactions</div>
            <Ticker rows={live.ticker} />
          </div>
        </div>
      )}
      {live.liveAlerts.length > 0 && (
        <div>
          <div className="small muted" style={{ marginBottom: 6 }}>Alerts raised during this stream</div>
          <div className="row" style={{ gap: 6 }}>
            {live.liveAlerts.slice(0, 12).map((a) => (
              <Link key={a.account} to={`/institution/alerts/${a.id}`} className={`pill ${a.priority}`} style={{ textDecoration: "none" }}>{a.account} · {a.priority}</Link>))}
          </div>
        </div>
      )}
      {live.injected.length > 0 && <div className="small muted">Injected so far: {live.injected.map((p) => `${PATTERN_ICON[p.pattern] || ""} ${p.pattern.replaceAll("_", " ")}`).join(" · ")}</div>}
    </div>
  );
}
