import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fin } from "../api.js";

// Spend Guard runs across every FinLens page: live alert pop-ups (with one-tap answers that teach
// the guard), browser notifications outside quiet hours, the night-summary reminder and the demo day.
const Ctx = createContext(null);
export const useGuard = () => useContext(Ctx) || { unread: 0, version: 0, demo: {}, act: async () => {}, ingested: () => {} };

const ACTIONS = {
  want: [["need", "It's a need"], ["regret", "😕 Regret"], ["worth", "👍 Worth it"]],
  check: [["need", "Need"], ["want", "Want"]],
  fraud: [["confirm", "✅ It was me"], ["dispute", "🚨 Not me!"]],
  duplicate: [["confirm", "Paid twice on purpose"], ["dispute", "🚨 Report it"]],
  scam: [["done", "Got it, won't approve"]],
};
const SEV_BG = { alert: "linear-gradient(135deg,#ef476f,#ff6b4a)", warn: "linear-gradient(135deg,#ff9233,#ffb020)", info: "#10292d" };

function GuardToasts({ toasts, dismiss, act }) {
  const nav = useNavigate();
  return (
    <div className="guard-toasts">
      {toasts.map((t) => (
        <div key={t.key} className="toast" style={{ background: SEV_BG[t.severity] || SEV_BG.info }}>
          <div className="row between" style={{ alignItems: "flex-start", gap: 8 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 800 }}>{t.title}</div>
              <div className="small" style={{ opacity: .96, marginTop: 3 }}>{t.body}</div>
            </div>
            <button className="small" style={{ background: "rgba(255,255,255,.2)", boxShadow: "none", padding: "2px 8px" }} onClick={() => dismiss(t.key)}>✕</button>
          </div>
          {t.result && <div className="small" style={{ marginTop: 8, background: "rgba(255,255,255,.18)", borderRadius: 10, padding: "6px 8px" }}>
            {t.result.rule && <div>✓ {t.result.rule}</div>}
            {t.result.steps && <ol style={{ margin: 0, paddingLeft: 18 }}>{t.result.steps.map((s) => <li key={s}>{s}</li>)}</ol>}
            {t.result.note && <div>{t.result.note}</div>}
          </div>}
          {!t.result && (
            <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
              {(ACTIONS[t.kind] || [["seen", "OK"]]).map(([a, label]) => (
                <button key={a} className="small" style={{ background: "#fff", color: "var(--ink)", boxShadow: "none" }} onClick={() => act(t, a)}>{label}</button>))}
              {t.link && <button className="small" style={{ background: "rgba(255,255,255,.2)", boxShadow: "none" }} onClick={() => { nav(t.link); dismiss(t.key); }}>Open →</button>}
            </div>)}
        </div>))}
    </div>
  );
}

export function GuardProvider({ enabled, children }) {
  const [toasts, setToasts] = useState([]);
  const [unread, setUnread] = useState(0);
  const [version, setVersion] = useState(0);
  const [demo, setDemo] = useState({ running: false, step: 0, total: 12, last: null });
  const lastId = useRef(null);
  const settings = useRef({ notify_browser: true, voice_summary: true, summary_time: "21:30" });
  const timer = useRef(null);

  const dismiss = (key) => setToasts((t) => t.filter((x) => x.key !== key));
  const show = useCallback((alerts) => {
    if (!alerts?.length) return;
    alerts.forEach((a) => { if (a.id && a.id > (lastId.current || 0)) lastId.current = a.id; });
    const fresh = alerts.filter((a) => a.status === "new" || a.status === undefined);
    setToasts((t) => [...fresh.map((a) => ({ ...a, key: `a${a.id}`, link: "/finlens/guard" })), ...t].slice(0, 4));
    fresh.forEach((a) => {
      if (a.severity !== "alert") setTimeout(() => dismiss(`a${a.id}`), 14000);
      if (!a.quiet && settings.current.notify_browser && "Notification" in window && Notification.permission === "granted") {
        try { new Notification(a.title, { body: a.body, tag: `finlens-${a.id}` }); } catch { /* not supported */ }
      }
    });
    setUnread((u) => u + fresh.length);
    setVersion((v) => v + 1);
  }, []);

  const poll = useCallback(async () => {
    try {
      const r = await fin.get(`/guard/alerts?since_id=${lastId.current || 0}`);
      if (lastId.current === null) { lastId.current = r.alerts[0]?.id || 0; setUnread(r.unread); return; }  // don't replay history
      setUnread(r.unread);
      if (r.alerts.length) show([...r.alerts].reverse());
    } catch { /* offline or logged out */ }
  }, [show]);

  useEffect(() => {
    if (!enabled) return;
    fin.get("/guard/settings").then((s) => { settings.current = s.settings; }).catch(() => {});
    poll();
    const id = setInterval(() => { if (!document.hidden) poll(); }, 10000);
    return () => clearInterval(id);
  }, [enabled, poll]);

  // night summary reminder, once a day at the user's chosen time (while FinLens is open)
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      const s = settings.current;
      if (!s.voice_summary || !s.summary_time) return;
      const now = new Date();
      const hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
      const key = `finlens_summary_${now.toDateString()}`;
      if (hhmm >= s.summary_time && !localStorage.getItem(key)) {
        localStorage.setItem(key, "1");
        setToasts((t) => [{ key: `sum${Date.now()}`, kind: "summary", severity: "info", title: "🌙 Your day in money is ready",
          body: "See where today's money went, and listen to it in your language.", link: "/finlens/guard" }, ...t]);
      }
    }, 60000);
    return () => clearInterval(id);
  }, [enabled]);

  const act = async (t, action) => {
    try {
      const r = await fin.post(`/guard/alerts/${t.id}`, { action });
      if (["dispute", "need", "want"].includes(action) || r.rule) setToasts((x) => x.map((y) => (y.key === t.key ? { ...y, result: r } : y)));
      else dismiss(t.key);
      if (action === "dispute") setTimeout(() => dismiss(t.key), 60000);
      setUnread((u) => Math.max(0, u - 1));
      setVersion((v) => v + 1);
    } catch { dismiss(t.key); }
  };

  const ingested = useCallback((results) => {  // results of /guard/sms, /guard/quick, /guard/demo/next
    const alerts = (results || []).flatMap((x) => x.alerts || []);
    show(alerts);
    setVersion((v) => v + 1);
  }, [show]);

  const step = useCallback(async () => {
    const r = await fin.post("/guard/demo/next");
    setDemo((d) => ({ ...d, step: r.step, total: r.total, last: r.sms ? { sms: r.sms, kind: r.kind } : d.last, running: d.running && !r.done && !!r.sms }));
    if (r.results) ingested(r.results);
    if (r.done || !r.sms) { clearInterval(timer.current); setDemo((d) => ({ ...d, running: false })); }
    return r;
  }, [ingested]);
  const startDemo = (seconds = 5) => {
    clearInterval(timer.current);
    setDemo((d) => ({ ...d, running: true }));
    step();
    timer.current = setInterval(step, seconds * 1000);
  };
  const stopDemo = () => { clearInterval(timer.current); setDemo((d) => ({ ...d, running: false })); };
  const resetDemo = async () => { stopDemo(); await fin.post("/guard/demo/reset"); setDemo({ running: false, step: 0, total: 12, last: null }); setVersion((v) => v + 1); };
  useEffect(() => () => clearInterval(timer.current), []);

  const value = { unread, version, act, ingested, setSettings: (s) => { settings.current = s; },
    demo: { ...demo, start: startDemo, stop: stopDemo, next: step, reset: resetDemo }, clearUnread: () => setUnread(0) };
  return (
    <Ctx.Provider value={value}>
      {children}
      <GuardToasts toasts={toasts} dismiss={dismiss} act={act} />
    </Ctx.Provider>
  );
}
