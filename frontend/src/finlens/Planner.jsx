import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fin, inr, kfmt } from "../api.js";
import { CountUp, Skeleton } from "../ui.jsx";

const GOAL_EMOJI = (n) => (/laptop|phone|mac/i.test(n) ? "💻" : /trip|travel|goa/i.test(n) ? "✈️" : /emergency|fund/i.test(n) ? "🛟" : /bike|car/i.test(n) ? "🛵" : "🎯");

export default function Planner() {
  const [data, setData] = useState(null);
  const [fc, setFc] = useState(null);
  const [incl, setIncl] = useState(false);
  const [form, setForm] = useState({ name: "", target: "", saved: "", target_date: "" });
  const [price, setPrice] = useState("");
  const [wi, setWi] = useState(null);
  const [err, setErr] = useState("");
  const load = () => fin.get("/goals").then(setData);
  useEffect(() => { load(); }, []);
  const [room, setRoom] = useState(null);
  useEffect(() => { fin.get("/spending/room").then(setRoom).catch(() => {}); }, [data]);
  useEffect(() => { fin.get(`/forecast?include_income=${incl}`).then(setFc).catch((e) => setErr(e.message)); }, [incl]);
  const add = async (e) => {
    e.preventDefault(); setErr("");
    try {
      setData(await fin.post("/goals", { name: form.name, target: +form.target, saved: +(form.saved || 0), target_date: form.target_date || null }));
      setForm({ name: "", target: "", saved: "", target_date: "" });
    } catch (x) { setErr(x.message); }
  };
  const setContribution = async (g, c) => setData(await fin.patch(`/goals/${g.id}`, { name: g.name, target: g.target, saved: g.saved, target_date: g.target_date, monthly_contribution: c }));
  if (!data) return <Skeleton rows={3} />;
  const fcSeries = fc ? [{ d: fc.as_of, v: fc.start_balance ?? fc.projected_month_end_balance + fc.projected_spend }, { d: fc.month_end, v: fc.projected_month_end_balance, lo: fc.range_80pct[0], hi: fc.range_80pct[1] }] : [];
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Planner 🎯</h1><p className="muted">Goals, forecasts and “can I afford it?” checks, all calculated from your data.</p></div></div>
      {err && <div className="error">{err}</div>}
      {room?.goal_warning && (
        <div className="card warn reveal">
          <h3>🎯 Is your goal plan realistic?</h3>
          <p style={{ margin: 0 }}>{room.goal_warning.text}</p>
          <p className="small muted" style={{ marginBottom: 0 }}>While the goal is reserved, FinLens keeps your suggested budgets tight. Extend the date or lower the target below to free up day-to-day money.</p>
        </div>)}
      <div className="grid g3 stagger">
        <div className="card hero sun stat">
          <div className="label">Avg monthly surplus</div>
          <div className="value">{data.avg_monthly_surplus != null && data.avg_monthly_surplus < 0 ? <>−<CountUp value={-data.avg_monthly_surplus} format={inr} /></> : <CountUp value={data.avg_monthly_surplus ?? data.monthly_capacity_estimate} format={inr} />}</div>
          <div className="sub">{data.avg_monthly_surplus != null && data.avg_monthly_surplus < 0 ? "you spend more than comes in each month (complete months)" : "income minus spending, complete months"}</div>
        </div>
        <div className="card lift">
          <h3>🛒 Planned-purchase check</h3>
          <form className="row" onSubmit={async (e) => { e.preventDefault(); setWi(await fin.post("/whatif", { price: +price })); }}>
            <input type="number" min="1" placeholder="Price ₹" value={price} onChange={(e) => setPrice(e.target.value)} style={{ width: 130 }} />
            <button className="warm">Check</button>
          </form>
          {wi && <div className={`notice ${wi.affordable_now ? "info" : ""}`} style={{ marginTop: 10 }}>{wi.affordable_now ? "✅ " : "⚠️ "}{wi.note} Safe-to-spend {inr(wi.safe_to_spend)} → {inr(wi.after_purchase)} after.</div>}
        </div>
        <div className="card lift">
          <h3>🔮 Month-end forecast</h3>
          {fc && <><div className="stat"><div className="value" style={{ color: "var(--sky)" }}><CountUp value={fc.projected_month_end_balance} format={inr} /></div>
            <div className="sub">80% range {inr(fc.range_80pct[0])} – {inr(fc.range_80pct[1])}</div></div>
            <label className="row small" style={{ marginTop: 8 }}><input type="checkbox" checked={incl} onChange={(e) => setIncl(e.target.checked)} /> include expected income</label></>}
        </div>
      </div>
      {fc && (
        <div className="card lift reveal">
          <h3>📉 Projected balance to {fc.month_end}</h3>
          <div style={{ height: 200 }}>
            <ResponsiveContainer>
              <AreaChart data={fcSeries}>
                <defs><linearGradient id="fg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#1e9bf0" stopOpacity={0.4} /><stop offset="100%" stopColor="#0fb5ae" stopOpacity={0.05} /></linearGradient></defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0e6d8" /><XAxis dataKey="d" /><YAxis tickFormatter={kfmt} />
                <Tooltip formatter={(v) => inr(v)} />
                <ReferenceLine y={0} stroke="#ef476f" strokeDasharray="4 4" />
                <Area dataKey="v" name="Balance" stroke="#1e9bf0" strokeWidth={3} fill="url(#fg)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <ul className="small muted">{fc.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </div>
      )}
      <div className="grid g2">
        <form className="card stack reveal" onSubmit={add}>
          <h3>➕ New savings goal</h3>
          <label className="field">What are you saving for?<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Laptop, trip to Goa, emergency fund…" /></label>
          <div className="row">
            <label className="field" style={{ flex: 1 }}>Target ₹<input required type="number" min="1" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} /></label>
            <label className="field" style={{ flex: 1 }}>Already saved ₹<input type="number" min="0" value={form.saved} onChange={(e) => setForm({ ...form, saved: e.target.value })} /></label>
          </div>
          <label className="field">Target date (optional)<input type="date" value={form.target_date} onChange={(e) => setForm({ ...form, target_date: e.target.value })} /></label>
          <button>Add goal</button>
        </form>
        <div className="stack">
          {data.goals.length === 0 && <div className="card muted reveal">No goals yet. Add one to see contribution plans and timelines. 🌱</div>}
          {data.goals.map((g) => (
            <div key={g.id} className="card lift reveal">
              <div className="row between"><h3 style={{ margin: 0 }}>{GOAL_EMOJI(g.name)} {g.name}</h3>
                <button className="small subtle" onClick={async () => setData(await fin.del(`/goals/${g.id}`))}>Remove</button></div>
              {g.pace && (
                <div className="row" style={{ gap: 6, margin: "6px 0" }}>
                  <span className={`pill ${({ ahead: "green", on_track: "green", achieved: "green", behind: "amber", overdue: "red" })[g.pace.status] || ""}`}>
                    {({ ahead: "🚀 ahead of plan", on_track: "✅ on track", achieved: "🏆 achieved", behind: "⚠️ behind plan", overdue: "⏰ past deadline", no_deadline: "no deadline" })[g.pace.status]}
                  </span>
                  {g.pace.days_left != null && g.pace.status !== "achieved" && (g.pace.days_left >= 0
                    ? <span className="pill">{g.pace.days_left} day{g.pace.days_left === 1 ? "" : "s"} left</span>
                    : <span className="pill red">{-g.pace.days_left} days past the deadline</span>)}
                  {g.pace.required_monthly != null && g.pace.status !== "achieved" && <span className="pill blue">needs {inr(g.pace.required_monthly)}/mo · {inr(g.pace.required_daily)}/day</span>}
                </div>)}
              <div className="row between small" style={{ margin: "6px 0" }}><span className="num">{inr(g.saved)} of {inr(g.target)}</span><b>{g.plan.progress_pct}%</b></div>
              <div className="progress" style={{ position: "relative" }}>
                <i style={{ width: `${Math.min(100, g.plan.progress_pct)}%` }} />
                {g.pace?.expected_by_now != null && <span title={`Plan says ${inr(g.pace.expected_by_now)} by today`}
                  style={{ position: "absolute", top: -3, bottom: -3, width: 3, borderRadius: 2, background: "var(--ink)", left: `${Math.min(100, g.pace.expected_by_now / g.target * 100)}%` }} />}
              </div>
              {g.pace?.status === "behind" && <p className="small" style={{ margin: "6px 0 0" }}>You're {inr(-g.pace.gap)} behind where the plan expects today. FinLens tightens your suggested budgets until you catch up.</p>}
              <form className="row" style={{ marginTop: 8 }} onSubmit={async (e) => { e.preventDefault(); const v = +e.target.amt.value; if (v > 0) { setData(await fin.post(`/goals/${g.id}/deposit`, { amount: v })); e.target.reset(); } }}>
                <input name="amt" type="number" min="1" placeholder="₹ add savings" style={{ width: 140 }} />
                <button className="small warm">+ Add to goal</button>
              </form>
              {g.plan.required_monthly != null && (
                <p className="small" style={{ marginBottom: 0 }}>To reach it by <b>{g.target_date}</b>: <b>{inr(g.plan.required_monthly)}/month</b> for {g.plan.months_to_target} month{g.plan.months_to_target === 1 ? "" : "s"}{" "}
                  {g.plan.feasible ? <span className="pill green">fits your surplus</span> : <span className="pill amber">above your current surplus</span>}</p>)}
              <div className="divider" />
              {g.plan.overdue && <p className="small" style={{ marginBottom: 0 }}>⏰ The deadline has passed with {inr(g.plan.remaining)} still to go. Pick a new date (edit the goal) or choose a plan below.</p>}
              {g.plan.alternatives_note && <div className="notice small" style={{ marginTop: 6 }}>{g.plan.alternatives_note}</div>}
              {g.plan.alternatives.length > 0 && <div className="small muted">Alternative plans ({g.plan.alternatives_basis}):</div>}
              <div className="grid g3" style={{ gap: 8, marginTop: 6 }}>
                {g.plan.alternatives.map((a) => (
                  <button key={a.plan} type="button" className="subtle" style={{ flexDirection: "column", alignItems: "flex-start", boxShadow: g.monthly_contribution === a.monthly_contribution ? "inset 0 0 0 2px var(--emerald)" : undefined }}
                    onClick={() => setContribution(g, a.monthly_contribution)}>
                    <span className="small">{a.plan}</span><b className="num">{inr(a.monthly_contribution)}/mo</b><span className="small muted">{a.months} month{a.months === 1 ? "" : "s"} · {a.reach_by}</span>
                  </button>))}
              </div>
              {g.monthly_contribution != null && <p className="small muted" style={{ marginBottom: 0 }}>Reserving {inr(g.monthly_contribution)}/month in your safe-to-spend.</p>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
