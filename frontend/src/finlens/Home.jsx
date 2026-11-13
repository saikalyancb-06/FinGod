import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fin, inr, kfmt } from "../api.js";
import { CAT_COLORS, CAT_EMOJI, CountUp, Skeleton, Stat } from "../ui.jsx";
import Heatmap from "./Heatmap.jsx";
import ForYou, { HealthBanner } from "./ForYou.jsx";
import GuardToday from "./GuardToday.jsx";

export default function Home({ me, onNudges }) {
  const [d, setD] = useState(null);
  const [month, setMonth] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { fin.get("/dashboard" + (month ? `?month=${month}` : "")).then(setD).catch((e) => setErr(e.message)); }, [month]);
  if (err) return <div className="error">{err}</div>;
  if (!d) return <Skeleton rows={4} />;
  const cur = d.monthly.find((m) => m.month === d.month) || d.monthly.at(-1);
  const sts = d.safe_to_spend;
  const hour = new Date().getHours();
  return (
    <div className="stack">
      <div className="page-head reveal">
        <div>
          <h1>{hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening"}, {me.name.split(" ")[0]} ☀️</h1>
          <p className="muted">Statement data up to {d.as_of}. Every figure here is calculated from your uploaded transactions.</p>
        </div>
        <select value={d.month} onChange={(e) => setMonth(e.target.value)}>{d.months.map((m) => <option key={m}>{m}</option>)}</select>
      </div>

      <HealthBanner h={d.health} />
      <GuardToday />
      <ForYou onCount={onNudges} />

      <div className="grid g4 stagger">
        <div className="card hero stat">
          <div className="label">Safe to spend</div>
          <div className="value">{sts ? <CountUp value={sts.safe_to_spend} format={inr} /> : "—"}</div>
          <div className="sub">{sts?.period || "until month end"}, after bills, goal savings &amp; buffer</div>
        </div>
        <Stat color="sky" icon="💰" label="Balance" value={d.balance.balance} format={inr} sub={d.balance.source} />
        <Stat color="emerald" icon="📥" label={`Inflows · ${d.month}`} value={cur.inflow_total} format={inr} sub={`Income ${inr(cur.income)}. Refunds/transfers aren't counted as income.`} />
        <Stat color="coral" icon="📤" label={`Spending · ${d.month}`} value={cur.outflow} format={inr} sub={`Essential ${inr(cur.essential)} · discretionary ${inr(cur.discretionary)}`} />
      </div>

      {d.insight && (
        <div className="card hero warm reveal">
          <h3 style={{ color: "#fff" }}>💡 FinLens insight</h3>
          <p style={{ margin: 0, fontSize: 15, maxWidth: 820 }}>{d.insight}</p>
          <div className="row" style={{ marginTop: 14, position: "relative", zIndex: 1 }}>
            <Link className="btn small" style={{ background: "#fff", color: "var(--coral)" }} to={`/finlens/ask?q=${encodeURIComponent(d.health?.is_running_out ? "Why am I running out of money?" : "How am I doing this month?")}`}>{d.health?.is_running_out ? "Ask why →" : "How am I doing? →"}</Link>
            <Link className="btn small" style={{ background: "rgba(255,255,255,.2)" }} to="/finlens/spending">Find cheaper options</Link>
          </div>
        </div>
      )}
      {d.uncertain_count > 0 && <div className="notice reveal">❔ {d.uncertain_count} transaction(s) are labelled <b>Uncertain</b>. <Link to="/finlens/transactions?category=Uncertain">Review and correct them</Link> to sharpen your totals.</div>}

      <div className="grid g2 stagger">
        <div className="card lift">
          <h3><span className="chip-ico" style={{ background: "var(--coral-bg)" }}>🍩</span>Where your money went · {d.month}</h3>
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie data={d.categories} dataKey="total" nameKey="category" innerRadius={62} outerRadius={104} paddingAngle={2} cornerRadius={6} animationDuration={900}>
                  {d.categories.map((c) => <Cell key={c.category} fill={CAT_COLORS[c.category] || "#999"} stroke="none" />)}
                </Pie>
                <Tooltip formatter={(v) => inr(v)} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="stack" style={{ marginTop: 6 }}>
            {d.categories.map((c) => (
              <div key={c.category}>
                <div className="row between"><span>{CAT_EMOJI[c.category]} {c.category} <span className="muted small">· {c.count} txns</span></span><b className="num">{inr(c.total)} <span className="muted small">{c.share_pct}%</span></b></div>
                <div className="progress" style={{ height: 6 }}><i style={{ width: `${c.share_pct}%`, background: CAT_COLORS[c.category] }} /></div>
              </div>
            ))}
          </div>
        </div>
        <div className="stack">
          <div className="card lift">
            <h3><span className="chip-ico" style={{ background: "var(--mint-bg)" }}>📊</span>Income vs spending by month</h3>
            <div style={{ height: 250 }}>
              <ResponsiveContainer>
                <BarChart data={d.monthly} barGap={4}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0e6d8" />
                  <XAxis dataKey="month" tickLine={false} axisLine={false} /><YAxis tickFormatter={kfmt} tickLine={false} axisLine={false} />
                  <Tooltip formatter={(v) => inr(v)} cursor={{ fill: "#fff4dc" }} /><Legend />
                  <Bar dataKey="income" name="Income" fill="#00a676" radius={[8, 8, 0, 0]} />
                  <Bar dataKey="essential" name="Essential" stackId="s" fill="#1e9bf0" />
                  <Bar dataKey="discretionary" name="Discretionary" stackId="s" fill="#ff9233" radius={[8, 8, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="muted small" style={{ margin: 0 }}>The latest month may be partial (statement ends {d.as_of}).</p>
          </div>
          <div className="card lift">
            <h3><span className="chip-ico" style={{ background: "var(--saffron-bg)" }}>📅</span>Daily spending · {d.month}</h3>
            <div style={{ height: 130 }}>
              <ResponsiveContainer>
                <AreaChart data={d.daily}>
                  <defs><linearGradient id="dg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#ff6b4a" stopOpacity={0.5} /><stop offset="100%" stopColor="#ffb020" stopOpacity={0.05} /></linearGradient></defs>
                  <XAxis dataKey="date" hide /><YAxis hide />
                  <Tooltip formatter={(v) => inr(v)} />
                  <Area type="monotone" dataKey="spend" stroke="#ff6b4a" strokeWidth={2.5} fill="url(#dg)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </div>

      <div className="grid g3 stagger">
        <div className="card lift">
          <h3><span className="chip-ico" style={{ background: "var(--mint-bg)" }}>🧮</span>Safe-to-spend maths</h3>
          {sts ? (<>
            <dl className="kv">
              <dt>Available balance</dt><dd>{inr(sts.available_balance)}</dd>
              <dt>− Pending debits</dt><dd>{inr(sts.pending_debits)}</dd>
              <dt>− Upcoming commitments</dt><dd>{inr(sts.upcoming_commitments)}</dd>
              <dt>− Planned savings</dt><dd>{inr(sts.planned_savings)}</dd>
              <dt>− Emergency buffer</dt><dd>{inr(sts.emergency_buffer)}</dd>
              <dt><b>= Safe to spend</b></dt><dd style={{ color: "var(--emerald)", fontSize: 18 }}>{inr(sts.safe_to_spend)}</dd>
            </dl>
            {sts.shortfall > 0 && <div className="notice" style={{ marginTop: 8 }}>Shortfall of {inr(sts.shortfall)} after reserving commitments.</div>}
            <p className="muted small">{sts.assumption}</p></>) : <p className="muted">Add an opening balance in Settings to enable this.</p>}
        </div>
        <div className="card lift">
          <h3><span className="chip-ico" style={{ background: "var(--sky-bg)" }}>🔁</span>Upcoming recurring payments</h3>
          {d.recurring.length ? d.recurring.map((r) => (
            <div key={r.merchant_key} className="row between" style={{ padding: "8px 0", borderBottom: "1px dashed var(--line)" }}>
              <span>{CAT_EMOJI[r.category] || "•"} {r.merchant} <span className="pill blue">{r.cadence}</span></span>
              <span className="num"><b>{inr(r.typical_amount)}</b> <span className="muted small">~{r.next_expected.slice(5)}</span></span>
            </div>)) : <p className="muted">None detected.</p>}
          <p className="muted small">Spotted from repeated payments. Review each and keep what's useful.</p>
        </div>
        <div className="card lift">
          <h3><span className="chip-ico" style={{ background: "var(--saffron-bg)" }}>🔮</span>Month-end forecast</h3>
          {d.forecast ? (<>
            <div className="stat"><div className="value" style={{ color: "var(--sky)" }}><CountUp value={d.forecast.projected_month_end_balance} format={inr} /></div>
              <div className="sub">likely range {inr(d.forecast.range_80pct[0])} – {inr(d.forecast.range_80pct[1])} on {d.forecast.month_end}</div></div>
            <ul className="small muted" style={{ paddingLeft: 18, marginBottom: 0 }}>{d.forecast.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></>) : <p className="muted">Needs a balance.</p>}
        </div>
      </div>

      {d.heatmap?.length > 0 && (
        <div className="card lift reveal">
          <h3><span className="chip-ico" style={{ background: "var(--coral-bg)" }}>🔥</span>Spending calendar</h3>
          <Heatmap data={d.heatmap} period={d.period} />
        </div>
      )}

      {d.goals.length > 0 && (
        <div className="card lift reveal"><h3><span className="chip-ico" style={{ background: "var(--rose-bg)" }}>🎯</span>Goals</h3>
          {d.goals.map((g) => (
            <div key={g.id} style={{ marginBottom: 12 }}>
              <div className="row between"><b>{g.name}</b><span className="num">{inr(g.saved)} / {inr(g.target)} · {g.plan.progress_pct}%</span></div>
              <div className="progress"><i style={{ width: `${Math.min(100, g.plan.progress_pct)}%` }} /></div>
            </div>))}
        </div>
      )}
    </div>
  );
}
