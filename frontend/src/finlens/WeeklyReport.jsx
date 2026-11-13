import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { fin, inr } from "../api.js";
import { CAT_COLORS, CAT_EMOJI, CountUp, Skeleton } from "../ui.jsx";
import { SpeakButton } from "../voice.jsx";
import { HEALTH } from "./ForYou.jsx";

const fmtDay = (s) => new Date(s + "T00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric" });
const addDays = (s, n) => { const d = new Date(s + "T00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

function BuddyShare() {
  const [b, setB] = useState(null);
  useEffect(() => { fin.get("/guard/buddy").then(setB).catch(() => {}); }, []);
  if (!b) return null;
  return <a className="btn small ghost" href={b.whatsapp} target="_blank" rel="noreferrer" title={b.text}>📤 Share{b.buddy ? ` with ${b.buddy}` : ""}</a>;
}

export default function WeeklyReport({ me }) {
  const [r, setR] = useState(null);
  const [end, setEnd] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { setR(null); fin.get("/report/weekly" + (end ? `?end=${end}` : "")).then(setR).catch((e) => setErr(e.message)); }, [end]);
  if (err) return <div className="error">{err}</div>;
  if (!r) return <Skeleton rows={4} />;
  if (r.no_data) return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Your week in money 📰</h1><p className="muted">{fmtDay(r.period.start)} → {fmtDay(r.period.end)}</p></div>
        <button className="small" onClick={() => setEnd(null)}>Latest week →</button></div>
      <div className="notice">{r.message}</div>
    </div>);
  const cmp = r.change != null;
  const up = cmp && r.change > 0;
  const h = r.health && HEALTH[r.health.status];
  const maxCat = Math.max(1, ...r.categories.map((c) => c.spent));
  const speech = [`Your week from ${r.period.start} to ${r.period.end}.`, `You spent ${inr(r.spent)}` + (cmp ? `, ${up ? "up" : "down"} ${inr(Math.abs(r.change))} from the week before.` : "."),
    r.win && `Win: ${r.win}`, r.focus && `Focus: ${r.focus}`].filter(Boolean).join(" ");
  return (
    <div className="stack report">
      <div className="page-head reveal">
        <div><h1>Your week in money 📰</h1>
          <p className="muted">{fmtDay(r.period.start)} → {fmtDay(r.period.end)} · compared with the 7 days before</p></div>
        <div className="row no-print" style={{ gap: 8 }}>
          <button className="small ghost" onClick={() => setEnd(addDays(r.period.end, -7))}>← Previous week</button>
          {end && !r.is_latest_week && <button className="small ghost" onClick={() => setEnd(addDays(r.period.end, 7))}>Next week →</button>}
          <SpeakButton text={speech} />
          <button className="small" onClick={() => window.print()}>🖨️ Save as PDF</button>
          {r.is_latest_week && <BuddyShare />}
        </div>
      </div>

      <div className="grid g3 stagger">
        <div className="card hero stat">
          <div className="label">Spent this week</div>
          <div className="value"><CountUp value={r.spent} format={inr} /></div>
          <div className="sub">{r.transactions} payments · {r.change_pct != null ? `${up ? "▲" : "▼"} ${Math.abs(r.change_pct)}% vs last week` : "no previous week to compare"}</div>
        </div>
        {cmp ? (
          <div className="card stat lift" style={{ background: up ? "#fff1ec" : r.change === 0 ? "#f5f2ec" : "#e3f8ef" }}>
            <div className="label">{up ? "More than last week" : r.change === 0 ? "Same as last week" : "Less than last week"}</div>
            <div className="value" style={{ color: up ? "var(--coral)" : "var(--emerald)" }}>{r.change === 0 ? "" : up ? "+" : "−"}<CountUp value={Math.abs(r.change)} format={inr} /></div>
            <div className="sub">Last week: {inr(r.previous_week)}</div>
          </div>
        ) : (
          <div className="card stat lift" style={{ background: "#f5f2ec" }}>
            <div className="label">vs last week</div><div className="value" style={{ fontSize: 22 }}>—</div>
            <div className="sub">The statement doesn't cover the whole week before, so there's no fair comparison.</div>
          </div>)}
        <div className="card stat lift" style={{ background: "#e4f3fe" }}>
          <div className="label">No-spend days</div>
          <div className="value" style={{ color: "var(--sky)" }}>{r.no_spend_days} <span className="small muted">/ {r.days_covered ?? 7}</span></div>
          <div className="sub">days without non-essential spending{r.income_received ? ` · ${inr(r.income_received)} came in` : ""}</div>
        </div>
      </div>

      <div className="grid g2 stagger">
        {r.win && <div className="card lift" style={{ borderLeft: "5px solid var(--emerald)" }}><h3>🏆 Win of the week</h3><p style={{ margin: 0, fontSize: 16 }}>{r.win}</p></div>}
        {r.focus && <div className="card lift" style={{ borderLeft: "5px solid var(--saffron, #ffb020)" }}><h3>🎯 Focus for next week</h3><p style={{ margin: 0, fontSize: 16 }}>{r.focus}</p></div>}
      </div>

      <div className="grid g2 stagger">
        <div className="card lift">
          <h3><span className="chip-ico" style={{ background: "var(--saffron-bg)" }}>📅</span>Day by day</h3>
          <div style={{ height: 200 }}>
            <ResponsiveContainer>
              <BarChart data={r.daily}>
                <XAxis dataKey="date" tickFormatter={fmtDay} tickLine={false} axisLine={false} fontSize={12} />
                <Tooltip formatter={(v) => inr(v)} labelFormatter={fmtDay} cursor={{ fill: "#fff4dc" }} />
                <Bar dataKey="spent" radius={[8, 8, 0, 0]} animationDuration={900}>
                  {r.daily.map((d) => <Cell key={d.date} fill={d.spent === 0 ? "#00a676" : d.spent > r.spent / 7 * 1.5 ? "#ff6b4a" : "#ffb020"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="small muted" style={{ margin: 0 }}>🟢 no spending · 🟠 normal · 🔴 heavy day (over 1.5× your daily average this week)</p>
        </div>
        <div className="card lift">
          <h3><span className="chip-ico" style={{ background: "var(--coral-bg)" }}>🍩</span>Where it went</h3>
          <div className="stack" style={{ gap: 10 }}>
            {r.categories.map((c) => (
              <div key={c.category}>
                <div className="row between"><span>{CAT_EMOJI[c.category] || "•"} {c.category} <span className="muted small">· {c.count}</span></span>
                  <span className="num"><b>{inr(c.spent)}</b> <span className="small" style={{ color: c.change > 0 ? "var(--coral)" : "var(--emerald)" }}>{c.change > 0 ? "▲" : c.change < 0 ? "▼" : ""}{c.change ? inr(Math.abs(c.change)) : ""}</span></span></div>
                <div className="progress" style={{ height: 7 }}><i style={{ width: `${(c.spent / maxCat) * 100}%`, background: CAT_COLORS[c.category] || "#999" }} /></div>
              </div>))}
            {!r.categories.length && <p className="muted">No spending this week. 🌱</p>}
          </div>
        </div>
      </div>

      <div className="grid g2 stagger">
        {r.biggest && (
          <div className="card lift">
            <h3>💸 Biggest payment</h3>
            <div className="row between"><span><b>{r.biggest.merchant}</b> <span className="muted small">· {r.biggest.category} · {fmtDay(r.biggest.date)}</span></span><b className="num" style={{ fontSize: 20 }}>{inr(r.biggest.amount)}</b></div>
            {!["Transfers & Other", "Accommodation & Utilities", "Education"].includes(r.biggest.category) && <p className="small muted" style={{ marginBottom: 0 }}>Shared with friends? <Link to="/finlens/split">Split it</Link> so only your share counts.</p>}
          </div>)}
        {r.health && (
          <div className="card" style={{ background: h.grad, color: "#fff", border: "none" }}>
            <h3 style={{ color: "#fff" }}>{h.emoji} Money health: {r.health.label}</h3>
            <p style={{ margin: 0 }}>{r.health.headline}</p>
            {r.safe_to_spend != null && <p className="small" style={{ marginBottom: 0, opacity: .9 }}>Safe to spend now: <b>{inr(r.safe_to_spend)}</b></p>}
          </div>)}
      </div>

      {r.goals.length > 0 && (
        <div className="card lift reveal">
          <h3>🎯 Goals</h3>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
            {r.goals.map((g) => (
              <div key={g.name}>
                <div className="row between"><b>{g.name}</b><span className={`pill ${g.status === "behind" || g.status === "overdue" ? "amber" : "green"}`}>{(g.status || "").replace("_", " ")}</span></div>
                <div className="progress" style={{ height: 8, marginTop: 6 }}><i style={{ width: `${Math.min(100, g.progress_pct || 0)}%`, background: "linear-gradient(90deg,#00a676,#1e9bf0)" }} /></div>
                <div className="small muted">{inr(g.saved)} of {inr(g.target)}{g.needs_per_month ? ` · needs ${inr(g.needs_per_month)}/month` : ""}</div>
              </div>))}
          </div>
        </div>)}
      <p className="small muted">All figures come from your statement{me?.name ? ` for ${me.name}` : ""}. Shared expenses count only your share.</p>
    </div>
  );
}
