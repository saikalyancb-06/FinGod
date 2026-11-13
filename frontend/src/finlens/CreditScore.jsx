import { useEffect, useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fin, inr } from "../api.js";
import { CountUp } from "../ui.jsx";

function Gauge({ score }) {
  const pct = score == null ? 0 : (score - 300) / 550;
  const r = 80, c = Math.PI * r;
  const color = score >= 780 ? "#00a676" : score >= 700 ? "#7cc243" : score >= 600 ? "#ffb020" : "#ff6b4a";
  return (
    <svg viewBox="0 0 200 120" style={{ width: "100%", maxWidth: 260 }}>
      <defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stopColor="#ff6b4a" /><stop offset=".45" stopColor="#ffb020" /><stop offset=".75" stopColor="#7cc243" /><stop offset="1" stopColor="#00a676" /></linearGradient></defs>
      <path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="#f4efe7" strokeWidth="16" strokeLinecap="round" />
      <path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#gg)" strokeWidth="16" strokeLinecap="round"
        strokeDasharray={`${c * pct} ${c}`} style={{ transition: "stroke-dasharray 1s cubic-bezier(.2,.8,.2,1)" }} />
      <text x="100" y="92" textAnchor="middle" style={{ font: "700 34px 'Space Grotesk'", fill: color }}>{score ?? "—"}</text>
      <text x="100" y="112" textAnchor="middle" style={{ font: "600 10px Manrope", fill: "#5f7175" }}>300 · · · 850</text>
    </svg>
  );
}

export function CashflowScore() {
  const [cut, setCut] = useState(0);
  const [inc, setInc] = useState(0);
  const [inv, setInv] = useState(0);
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const t = setTimeout(() => fin.post("/credit/score", { discretionary_cut_pct: cut, income_increase_pct: inc, monthly_investment: inv }).then(setD).catch((e) => setErr(e.message)), 180);
    return () => clearTimeout(t);
  }, [cut, inc, inv]);
  if (err) return <div className="error">{err}</div>;
  if (!d) return <div className="skeleton" />;
  const cur = d.current, wi = d.what_if;
  const delta = (wi.score ?? 0) - (cur.score ?? 0);
  return (
    <div className="card reveal">
      <div className="row between"><h3><span className="chip-ico" style={{ background: "var(--sky-bg)" }}>📈</span>Cash-flow score</h3><span className="pill">CredWise formula · not a bureau score</span></div>
      <div className="grid g3" style={{ alignItems: "center" }}>
        <div style={{ textAlign: "center" }}>
          <Gauge score={cur.score} />
          <div className="pill amber" style={{ textTransform: "capitalize" }}>{cur.band}</div>
          <div className="small muted" style={{ marginTop: 6 }}>Based on {d.months_used.join(", ")}</div>
        </div>
        <div className="stack">
          {cur.factors.map((f) => (
            <div key={f.factor}>
              <div className="row between small"><b>{f.factor}</b><b className="num" style={{ color: f.points >= 0 ? "var(--emerald)" : "var(--coral)" }}>{f.points >= 0 ? "+" : ""}{f.points}</b></div>
              <div style={{ display: "flex", height: 8, background: "#f4efe7", borderRadius: 999, overflow: "hidden" }}>
                <div style={{ flex: 1, display: "flex", justifyContent: "flex-end" }}>{f.points < 0 && <i style={{ width: `${Math.min(100, -f.points / 1.2)}%`, background: "var(--coral)", borderRadius: 999, animation: "grow .9s both" }} />}</div>
                <div style={{ width: 2, background: "#cbd5d8" }} />
                <div style={{ flex: 1 }}>{f.points > 0 && <i style={{ display: "block", height: "100%", width: `${Math.min(100, f.points / 1.2)}%`, background: "var(--emerald)", borderRadius: 999, animation: "grow .9s both" }} />}</div>
              </div>
              <div className="small muted">{f.explanation}</div>
            </div>))}
        </div>
        <div className="card tint stack" style={{ boxShadow: "none" }}>
          <b>🔧 What if…</b>
          <label className="field">I cut discretionary spending by <b style={{ color: "var(--ink)" }}>{cut}%</b><input type="range" min="0" max="60" value={cut} onChange={(e) => setCut(+e.target.value)} style={{ padding: 0, border: "none", accentColor: "var(--coral)" }} /></label>
          <label className="field">My income rises by <b style={{ color: "var(--ink)" }}>{inc}%</b><input type="range" min="0" max="50" value={inc} onChange={(e) => setInc(+e.target.value)} style={{ padding: 0, border: "none", accentColor: "var(--emerald)" }} /></label>
          <label className="field">I invest monthly <b style={{ color: "var(--ink)" }}>{inr(inv)}</b><input type="range" min="0" max="5000" step="100" value={inv} onChange={(e) => setInv(+e.target.value)} style={{ padding: 0, border: "none", accentColor: "var(--sky)" }} /></label>
          <div className="stat"><div className="label">Projected score</div><div className="value" style={{ color: delta > 0 ? "var(--emerald)" : "var(--ink)" }}>{wi.score != null ? <CountUp value={wi.score} format={(v) => Math.round(v)} duration={500} /> : "—"} <span className="small">{delta > 0 ? `▲ ${delta}` : ""}</span></div></div>
        </div>
      </div>
      <p className="small muted" style={{ marginBottom: 0 }}>{cur.method}. {cur.source}</p>
    </div>
  );
}

export function LenderModel({ principal, months, rate, affordability }) {
  const [f, setF] = useState({ purpose: "", product: "Personal Loan", cibil_score: "", credit_history_years: "", past_default: false,
    late_payments_2y: 0, negative_records: 0, existing_debt_outstanding: "", savings: "" });
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const run = async () => {
    setBusy(true); setErr("");
    const body = { principal: +principal, months: +months, annual_rate_pct: +rate, purpose: f.purpose, product: f.product,
      past_default: f.past_default, late_payments_2y: +f.late_payments_2y || 0, negative_records: +f.negative_records || 0 };
    for (const k of ["cibil_score", "credit_history_years", "existing_debt_outstanding", "savings"]) if (f[k] !== "") body[k] = +f[k];
    try { setR(await fin.post("/credit/lender-model", body)); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const tone = { likely: ["✅ Approval likely", "linear-gradient(135deg,#00a676,#7cc243)"], borderline: ["⚖️ Borderline", "linear-gradient(135deg,#ffb020,#ff9233)"],
    unlikely: ["⛔ Approval unlikely", "linear-gradient(135deg,#ff6b4a,#ef476f)"] };
  const sel = (k, opts) => <select value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })}>{opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>;
  const num = (k, label, ph) => <label className="field" key={k}>{label}<input type="number" min="0" placeholder={ph} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>;
  return (
    <div className="card reveal stack">
      <div className="row between"><h3 style={{ margin: 0 }}><span className="chip-ico" style={{ background: "var(--saffron-bg)" }}>🏛️</span>Would a lender approve this?</h3>
        <span className="pill amber">built-in model · side signal</span></div>
      <p className="small muted" style={{ margin: 0 }}>FinLens's own lender-style model ({"≈"}92% accurate on 10,000 test applications). Income, EMIs and savings come from your statement and profile; add anything you know below for a sharper estimate.</p>
      <div className="grid g4" style={{ gap: 10 }}>
        <label className="field">Purpose{sel("purpose", [["", "Auto"], ["Education", "Education"], ["Personal", "Personal"], ["Medical", "Medical"], ["Business", "Business"], ["Home Improvement", "Home improvement"], ["Debt Consolidation", "Debt consolidation"]])}</label>
        <label className="field">Product{sel("product", [["Personal Loan", "Personal loan"], ["Credit Card", "Credit card"], ["Line of Credit", "Line of credit"]])}</label>
        {num("cibil_score", "CIBIL score (optional)", "e.g. 720")}
        {num("credit_history_years", "Credit history (years)", "0 if none")}
        {num("existing_debt_outstanding", "Debt still owed ₹ (optional)", "auto")}
        {num("savings", "Savings ₹ (optional)", "auto: balance")}
        {num("late_payments_2y", "Late payments (2 yrs)", "0")}
        <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={f.past_default} onChange={(e) => setF({ ...f, past_default: e.target.checked })} /><span className="small">I've defaulted on a loan before</span></label>
      </div>
      <div><button disabled={busy} onClick={run}>{busy ? "Estimating…" : "Estimate lender approval"}</button></div>
      {err && <div className="error">{err}</div>}
      {r && !r.available && <div className="notice">{r.message}</div>}
      {r?.status === "insufficient_information" && <div className="notice">{r.message}</div>}
      {r?.approval_probability != null && (
        <div className="stack">
          <div className="card" style={{ background: tone[r.status][1], color: "#fff", border: "none" }}>
            <div className="row between">
              <div><h2 style={{ color: "#fff", margin: 0 }}>{tone[r.status][0]}</h2>
                <div className="small" style={{ opacity: .9 }}>{r.inputs.product} · {r.inputs.purpose} · EMI {inr(r.inputs.emi)} · income {inr(r.inputs.monthly_income)}/mo (from {r.inputs.income_source})</div></div>
              <div className="num" style={{ fontSize: 42, fontWeight: 700 }}><CountUp value={r.approval_probability * 100} format={(v) => `${Math.round(v)}%`} duration={600} /></div>
            </div>
          </div>
          {affordability && affordability !== "eligible" && r.status === "likely" && (
            <div className="notice">⚠️ A lender might say yes, but FinLens's affordability check says this loan is <b>{affordability.replaceAll("_", " ")}</b> for your cash flow. Approval isn't the same as being able to repay comfortably.</div>)}
          <div className="grid g2">
            <div className="card">
              <h3>🧩 What's driving it</h3>
              {r.factors.map((x) => (
                <div key={x.factor} style={{ marginBottom: 8 }}>
                  <div className="row between small"><span>{x.factor}{x.value != null ? <span className="muted"> · {x.value}</span> : null}</span>
                    <b style={{ color: x.direction === "helps" ? "var(--emerald)" : "var(--coral)" }}>{x.direction === "helps" ? "▲ helps" : "▼ hurts"}</b></div>
                  <div className="progress" style={{ height: 6 }}><i style={{ width: `${Math.min(100, Math.abs(x.effect) * 25)}%`, background: x.direction === "helps" ? "var(--emerald)" : "var(--coral)" }} /></div>
                </div>))}
            </div>
            <div className="card">
              <h3>📈 Approval by credit score</h3>
              <div style={{ height: 170 }}>
                <ResponsiveContainer>
                  <BarChart data={r.credit_score_curve}>
                    <XAxis dataKey="credit_score" /><YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} /><Tooltip formatter={(v) => `${Math.round(v * 100)}%`} />
                    <Bar dataKey="approval" radius={[6, 6, 0, 0]}>{r.credit_score_curve.map((c) => <Cell key={c.credit_score} fill={c.approval >= 0.7 ? "#00a676" : c.approval >= 0.4 ? "#ffb020" : "#ef476f"} />)}</Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="small muted">Score used: {r.inputs.credit_score_used}{r.inputs.credit_score_assumed ? " (assumed)" : ""}</div>
            </div>
          </div>
          {r.tips.length > 0 && <div className="card tint"><h3>💡 How to improve your chances</h3><ul style={{ margin: 0 }}>{r.tips.map((t) => <li key={t}>{t}</li>)}</ul></div>}
          {r.notes.map((n) => <div key={n} className="small muted">ℹ️ {n}</div>)}
          {r.external_credwise && <details className="small"><summary>Original CredWise API response</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(r.external_credwise, null, 2)}</pre></details>}
          <div className="notice small">⚖️ {r.caveat}</div>
        </div>
      )}
    </div>
  );
}
