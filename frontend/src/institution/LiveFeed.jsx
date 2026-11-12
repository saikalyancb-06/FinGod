import { useEffect, useRef, useState } from "react";
import { inst } from "../api.js";

// Live ingest compatible with the team's mr-mule feed (ws, {"type":"csv","row":"..."}) and MuleGuard's
// live mode (raw CSV lines or {row}). Rows are batched every 2 s and appended to a live dataset.
export default function LiveFeed({ dsId, setDsId, onBatch }) {
  const [url, setUrl] = useState("ws://localhost:3000");
  const [state, setState] = useState("idle");
  const [count, setCount] = useState(0);
  const [log, setLog] = useState([]);
  const buf = useRef([]);
  const ws = useRef(null);
  const replay = useRef(null);
  const target = useRef(dsId);
  const flushing = useRef(false);

  useEffect(() => {
    const t = setInterval(async () => {
      if (!buf.current.length || !target.current || flushing.current) return;
      const rows = buf.current.splice(0, 2000);
      flushing.current = true;
      try {
        const r = await inst.post(`/datasets/${target.current}/append`, { rows });
        setLog((l) => [`+${r.added} rows → ${r.summary?.alerts ?? 0} alerts, ${r.summary?.fraud_rings ?? 0} rings`, ...l].slice(0, 6));
        onBatch?.();
      } catch (e) { setLog((l) => [`error: ${e.message}`, ...l].slice(0, 6)); }
      flushing.current = false;
    }, 2000);
    return () => { clearInterval(t); ws.current?.close(); clearInterval(replay.current); };
  }, []); // eslint-disable-line

  const ensureDataset = async (name) => {
    const r = await inst.post("/datasets/live", { name });
    target.current = r.dataset_id;
    setDsId(r.dataset_id);
    setCount(0);
  };
  const push = (row) => { buf.current.push(row); setCount((c) => c + 1); };

  const connect = async () => {
    await ensureDataset(`Live feed ${new Date().toLocaleTimeString("en-IN")}`);
    const s = new WebSocket(url);
    ws.current = s;
    setState("connecting");
    s.onopen = () => setState("connected");
    s.onclose = () => setState("disconnected");
    s.onerror = () => setState("error");
    s.onmessage = (ev) => {
      let row = ev.data;
      try { const j = JSON.parse(ev.data); if (j.type && j.type !== "csv") return; row = j.row ?? row; } catch { /* raw CSV line */ }
      String(row).split(/\r?\n/).filter(Boolean).forEach(push);
    };
  };
  const startReplay = async () => {
    await ensureDataset("Replay of synthetic demo stream");
    const txt = await (await fetch("/x-inst/api/sample-dataset")).text();
    // demo file columns: txn_id,timestamp,from_account,to_account,amount,channel -> feed schema
    const lines = txt.trim().split(/\r?\n/).slice(1).map((l) => { const [id, ts, f, t, a] = l.split(","); return `${id},${f},${t},${a},${ts}`; });
    let i = 0;
    setState("replaying");
    replay.current = setInterval(() => {
      lines.slice(i, i + 400).forEach(push);
      i += 400;
      if (i >= lines.length) { clearInterval(replay.current); setState("replay finished"); }
    }, 1000);
  };
  const stop = () => { ws.current?.close(); clearInterval(replay.current); setState("stopped"); };
  const live = ["connecting", "connected", "replaying"].includes(state);
  return (
    <div className="card stack">
      <div className="row between">
        <h3><span className="chip-ico" style={{ background: "var(--coral-bg)" }}>📡</span>Live transaction feed</h3>
        <span className={`pill ${live ? "red" : ""}`}>{live ? "● " : ""}{state} · {count} rows</span>
      </div>
      <p className="small muted" style={{ margin: 0 }}>Connect to a WebSocket that emits <code>transaction_id,sender_id,receiver_id,amount,timestamp</code> rows, such as the team's <b>mr-mule</b> feed (<code>npm start</code> in <code>reference/mr-mule</code>, port 3000). Rows are analysed every 2 seconds.</p>
      <div className="row">
        <input style={{ flex: 1, minWidth: 200 }} value={url} onChange={(e) => setUrl(e.target.value)} />
        {!live ? <><button className="small" onClick={connect}>Connect</button><button className="small warm" onClick={startReplay}>▶ Replay synthetic stream</button></>
          : <button className="small danger" onClick={stop}>Stop</button>}
      </div>
      {log.length > 0 && <div className="small muted">{log.map((l, i) => <div key={i}>{l}</div>)}</div>}
    </div>
  );
}
