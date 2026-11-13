import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fin } from "../api.js";

const SEV = {
  alert: { bg: "linear-gradient(135deg,#ff5f6d,#ff9a5a)", fg: "#fff", ring: "#ff5f6d" },
  warn: { bg: "#fff4dc", fg: "var(--ink)", ring: "#ffb020" },
  info: { bg: "#e4f3fe", fg: "var(--ink)", ring: "#1e9bf0" },
  good: { bg: "#e3f8ef", fg: "var(--ink)", ring: "#00a676" },
};

export const HEALTH = {
  healthy: { grad: "linear-gradient(135deg,#00a676,#2cc4a0)", emoji: "💚" },
  steady: { grad: "linear-gradient(135deg,#1e9bf0,#45c4e8)", emoji: "🙂" },
  tight: { grad: "linear-gradient(135deg,#ff9233,#ffb020)", emoji: "⚠️" },
  at_risk: { grad: "linear-gradient(135deg,#ff5f6d,#ff6b4a)", emoji: "🚨" },
  unknown: { grad: "linear-gradient(135deg,#8a9ba0,#5f7175)", emoji: "❔" },
};

export function HealthBanner({ h }) {
  if (!h) return null;
  const s = HEALTH[h.status] || HEALTH.steady;
  const pts = h.status === "healthy" || h.status === "steady" ? h.strengths : h.watch_outs;
  return (
    <div className="card reveal" style={{ background: s.grad, color: "#fff", border: "none", position: "relative", overflow: "hidden" }}>
      <div className="row between" style={{ alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div style={{ maxWidth: 760 }}>
          <div className="small" style={{ opacity: .9, letterSpacing: .4, textTransform: "uppercase", fontWeight: 700 }}>Money health</div>
          <h2 style={{ color: "#fff", margin: "4px 0 6px" }}>{s.emoji} {h.label}</h2>
          <p style={{ margin: 0, fontSize: 15 }}>{h.headline}</p>
          {pts?.length > 0 && <ul style={{ margin: "8px 0 0", paddingLeft: 18, opacity: .95 }}>{pts.slice(0, 3).map((p) => <li key={p} className="small">{p[0].toUpperCase() + p.slice(1)}</li>)}</ul>}
        </div>
        <div className="row" style={{ gap: 10 }}>
          {h.savings_rate_pct != null && (
            <div style={{ background: "rgba(255,255,255,.18)", borderRadius: 14, padding: "10px 14px", textAlign: "center", minWidth: 96 }}>
              <div className="num" style={{ fontSize: 24, fontWeight: 800 }}>{h.savings_rate_pct}%</div>
              <div className="small" style={{ opacity: .9 }}>{h.savings_rate_pct >= 0 ? "of money in kept" : "overspent"}</div>
            </div>)}
          {h.runway_days != null && (
            <div style={{ background: "rgba(255,255,255,.18)", borderRadius: 14, padding: "10px 14px", textAlign: "center", minWidth: 96 }}>
              <div className="num" style={{ fontSize: 24, fontWeight: 800 }}>{h.runway_days}</div>
              <div className="small" style={{ opacity: .9 }}>days of runway</div>
            </div>)}
        </div>
      </div>
    </div>
  );
}

export default function ForYou({ onCount }) {
  const [items, setItems] = useState(null);
  const [all, setAll] = useState(false);
  useEffect(() => { fin.get("/nudges").then((n) => { setItems(n); onCount?.(n.filter((x) => x.severity !== "good").length); }).catch(() => setItems([])); }, []); // eslint-disable-line
  const visible = items?.filter((n) => !n.id.startsWith("health_")); // the health banner above already says this
  if (!visible?.length) return null;
  const dismiss = async (id) => {
    const next = items.filter((n) => n.id !== id);
    setItems(next); onCount?.(next.filter((x) => x.severity !== "good").length);
    await fin.post(`/nudges/${encodeURIComponent(id)}/dismiss`).catch(() => {});
  };
  const shown = all ? visible : visible.slice(0, 4);
  return (
    <div className="card reveal">
      <div className="row between"><h3 style={{ margin: 0 }}><span className="chip-ico" style={{ background: "var(--saffron-bg)" }}>🔔</span>For you</h3>
        <Link className="small" to="/finlens/report">Weekly report →</Link></div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12, marginTop: 12 }}>
        {shown.map((n, i) => {
          const c = SEV[n.severity] || SEV.info;
          return (
            <div key={n.id} className="nudge" style={{ background: c.bg, color: c.fg, borderLeft: `4px solid ${c.ring}`, animationDelay: `${i * 0.06}s` }}>
              <div className="row between" style={{ alignItems: "flex-start" }}>
                <b>{n.icon} {n.title}</b>
                <button className="nudge-x" title="Dismiss" onClick={() => dismiss(n.id)} style={{ color: c.fg }}>×</button>
              </div>
              <div className="small" style={{ marginTop: 4, opacity: .92 }}>{n.body}</div>
              {n.action && <Link className="small" to={n.action.link} style={{ display: "inline-block", marginTop: 6, color: c.fg, fontWeight: 700 }}>{n.action.label} →</Link>}
            </div>);
        })}
      </div>
      {visible.length > 4 && <button className="small ghost" style={{ marginTop: 10 }} onClick={() => setAll(!all)}>{all ? "Show less" : `Show all ${visible.length}`}</button>}
    </div>
  );
}
