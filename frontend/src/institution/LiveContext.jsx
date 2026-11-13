import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { inst, inr } from "../api.js";

// One live connection for the whole investigator portal: streaming transactions,
// analysis summaries and real-time alert toasts survive page navigation.
const LiveCtx = createContext(null);
export const useLive = () => useContext(LiveCtx);

const PRIORITY_STYLE = { high: "linear-gradient(135deg,#ef476f,#ff6b4a)", medium: "linear-gradient(135deg,#ff9233,#ffb020)", low: "linear-gradient(135deg,#1e9bf0,#0fb5ae)" };

function Toasts({ toasts, dismiss }) {
  const nav = useNavigate();
  return (
    <div style={{ position: "fixed", top: 16, right: 16, zIndex: 50, display: "flex", flexDirection: "column", gap: 10, width: 360, maxWidth: "calc(100vw - 32px)" }}>
      {toasts.map((t) => (
        <div key={t.key} className="toast" style={{ background: t.kind === "alert" ? PRIORITY_STYLE[t.priority] || PRIORITY_STYLE.low : "#10292d" }}>
          <div className="row between" style={{ alignItems: "flex-start" }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 800 }}>{t.title}</div>
              <div className="small" style={{ opacity: .95, marginTop: 2 }}>{t.body}</div>
            </div>
            <button className="small" style={{ background: "rgba(255,255,255,.2)", boxShadow: "none", padding: "2px 8px" }} onClick={() => dismiss(t.key)}>✕</button>
          </div>
          {t.link && <button className="small" style={{ marginTop: 8, background: "#fff", color: "var(--ink)", boxShadow: "none" }}
            onClick={() => { nav(t.link); dismiss(t.key); }}>Open evidence →</button>}
        </div>
      ))}
    </div>
  );
}

export function LiveProvider({ children, onBatch }) {
  const [dsId, setDsId] = useState(() => Number(localStorage.getItem("inst_live_ds")) || null);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [ticker, setTicker] = useState([]);
  const [liveAlerts, setLiveAlerts] = useState([]);
  const [summary, setSummary] = useState(null);
  const [clock, setClock] = useState(null);
  const [sent, setSent] = useState(0);
  const [patterns, setPatterns] = useState({});
  const [injected, setInjected] = useState([]);
  const [toasts, setToasts] = useState([]);
  const ws = useRef(null);
  const onBatchRef = useRef(onBatch);
  onBatchRef.current = onBatch;

  const toast = useCallback((t) => {
    const key = Math.random().toString(36).slice(2);
    setToasts((x) => [{ ...t, key }, ...x].slice(0, 4));
    setTimeout(() => setToasts((x) => x.filter((y) => y.key !== key)), t.kind === "alert" ? 12000 : 6000);
  }, []);
  const dismiss = (key) => setToasts((x) => x.filter((y) => y.key !== key));

  const handle = useCallback((m) => {
    if (m.type === "hello") { setRunning(m.running); setPatterns(m.patterns || {}); }
    if (m.type === "status") { setRunning(m.status === "running"); if (m.status !== "running") toast({ kind: "info", title: "Live stream " + m.status, body: `${m.sent ?? ""} transactions streamed` }); }
    if (m.type === "batch") {
      setTicker((t) => [...(m.rows || []).slice().reverse(), ...t].slice(0, 40));
      setSummary(m.summary);
      if (m.sim_clock) setClock(m.sim_clock);
      if (m.sent != null) setSent(m.sent); else setSent((s) => s + (m.added || 0));
      onBatchRef.current?.();
    }
    if (m.type === "pattern") {
      setInjected((x) => [m, ...x].slice(0, 12));
      toast({ kind: "info", title: "🧪 Pattern injected", body: m.label });
    }
    if (m.type === "alert") {
      setLiveAlerts((x) => [m, ...x.filter((y) => y.account !== m.account)].slice(0, 30));
      toast({ kind: "alert", priority: m.priority, title: `🚨 ${m.escalated ? "Escalated" : "New"} ${m.priority} alert · ${m.account}`,
        body: m.headline || (m.rule_families || []).join(", "), link: `/institution/alerts/${m.id}` });
    }
  }, [toast]);

  useEffect(() => {
    if (!dsId) return undefined;
    localStorage.setItem("inst_live_ds", dsId);
    const token = localStorage.getItem("institution_token");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/x-inst/ws/live/${dsId}?token=${encodeURIComponent(token || "")}`);
    ws.current = sock;
    sock.onopen = () => setConnected(true);
    sock.onclose = () => setConnected(false);
    sock.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch { /* ignore */ } };
    const ping = setInterval(() => sock.readyState === 1 && sock.send("ping"), 20000);
    inst.get(`/live/${dsId}/status`).then((s) => { setRunning(s.running); setSent(s.sent); setPatterns(s.patterns); setInjected(s.injected.slice().reverse()); setClock(s.sim_clock); }).catch(() => {});
    return () => { clearInterval(ping); sock.close(); };
  }, [dsId, handle]);

  const start = async (opts) => {
    const r = await inst.post("/live/start", opts);
    setTicker([]); setLiveAlerts([]); setSent(0); setInjected([]); setSummary(null);
    setDsId(r.dataset_id); setRunning(true); setPatterns(r.patterns);
    return r.dataset_id;
  };
  const stop = async () => { if (dsId) await inst.post(`/live/${dsId}/stop`); setRunning(false); };
  const inject = (pattern) => inst.post(`/live/${dsId}/inject`, { pattern });
  const manual = (tx) => inst.post(`/live/${dsId}/tx`, tx);
  const watch = (id) => { setTicker([]); setLiveAlerts([]); setDsId(id); };

  return (
    <LiveCtx.Provider value={{ dsId, connected, running, ticker, liveAlerts, summary, clock, sent, patterns, injected, start, stop, inject, manual, watch, toast, inr }}>
      {children}
      <Toasts toasts={toasts} dismiss={dismiss} />
    </LiveCtx.Provider>
  );
}
