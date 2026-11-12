import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fin, inr, kfmt } from "../api.js";
import { CountUp } from "../ui.jsx";
import { CashflowScore, LenderModel } from "./CreditScore.jsx";

const STATUS = {
  eligible: ["✅ Likely affordable", "linear-gradient(135deg,#00a676,#7cc243)"],
  conditional: ["⚠️ Conditionally affordable", "linear-gradient(135deg,#ffb020,#ff9233)"],
  not_affordable_at_these_terms: ["⛔ Not affordable at these terms", "linear-gradient(135deg,#ff6b4a,#ef476f)"],
  insufficient_information: ["❔ Insufficient information", "linear-gradient(135deg,#8a9ba0,#5f7175)"],
};

export default function Credit({ me, onConsent }) {
  const [consent, setConsent] = useState(me.consents.credit);
  const [f, setF] = useState({ principal: 30000, annual_rate_pct: 14, months: 12, declared_obligations: 0 });
  const [emi, setEmi] = useState(null);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => fin.get(`/credit/emi?principal=${f.principal}&annual_rate_pct=${f.annual_rate_pct}&months=${f.months}`).then(setEmi).catch(() => setEmi(null)), 200);
    return () => clearTimeout(t);
  }, [f.principal, f.annual_rate_pct, f.months]);
  const assess = async () => {
    setErr(""); setBusy(true);
    try { setRes(await fin.post("/credit/assess", { ...f, principal: +f.principal, annual_rate_pct: +f.annual_rate_pct, months: +f.months, declared_obligations: +f.declared_obligations })); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const slider = (k, label, min, max, step, fmt) => (
    <label className="field">{label} <b style={{ color: "var(--ink)", fontSize: 15 }}>{fmt(f[k])}</b>
      <input type="range" min={min} max={max} step={step} value={f[k]} onChange={(e) => setF({ ...f, [k]: +e.target.value })} style={{ accentColor: "var(--emerald)", padding: 0, border: "none" }} />
    </label>
  );
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Credit check 🏦</h1><p className="muted">Estimate loan affordability from your cash flow, without a credit score. This is a separate purpose with separate consent.</p></div></div>
      {!consent && (
        <div className="card hero reveal">
          <h3 style={{ color: "#fff" }}>🔐 Separate consent needed</h3>
          <p style={{ margin: 0 }}>Credit assessment uses your income, expenses and obligations for a different purpose than spending insights. No fraud or AML data is ever used.</p>
          <button style={{ marginTop: 12, background: "#fff", color: "var(--emerald)", position: "relative", zIndex: 1 }}
            onClick={async () => { await fin.post("/consents", { purpose: "credit", granted: true }); setConsent(true); onConsent?.(); }}>I consent to a credit-affordability assessment</button>
        </div>
      )}
      <div className="grid g2 stagger">
        <div className="card stack">
          <h3>🧮 EMI simulator</h3>
          {slider("principal", "Loan amount", 5000, 500000, 5000, inr)}
          {slider("annual_rate_pct", "Interest rate (p.a.)", 6, 36, 0.5, (v) => `${v}%`)}
          {slider("months", "Tenure", 3, 60, 1, (v) => `${v} months`)}
          <label className="field">Other monthly obligations not in your statement (₹)
            <input type="number" min="0" value={f.declared_obligations} onChange={(e) => setF({ ...f, declared_obligations: e.target.value })} /></label>
          <button disabled={!consent || busy} onClick={assess}>{busy ? "Assessing…" : "Assess affordability"}</button>
          {err && <div className="error">{err}</div>}
        </div>
        <div className="card hero stat" style={{ display: "flex", flexDirection: "column", justifyContent: "center" }}>
          <div className="label">Monthly EMI</div>
          <div className="value" style={{ fontSize: 44 }}>{emi ? <CountUp value={emi.emi} format={inr} duration={500} /> : "—"}</div>
          {emi && <div className="sub" style={{ fontSize: 14 }}>Total interest {inr(emi.total_interest)} · total paid {inr(emi.total_payment)}</div>}
          <div className="sub small" style={{ marginTop: 10 }}>{emi?.formula}</div>
        </div>
      </div>
      {emi && (
        <div className="card reveal">
          <h3>📆 Repayment schedule</h3>
          <div style={{ height: 200 }}>
            <ResponsiveContainer>
              <BarChart data={emi.schedule}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0e6d8" /><XAxis dataKey="month" /><YAxis tickFormatter={kfmt} />
                <Tooltip formatter={(v) => inr(v)} /><Legend />
                <Bar dataKey="principal" stackId="e" fill="#00a676" name="Principal" />
                <Bar dataKey="interest" stackId="e" fill="#ff9233" name="Interest" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {res && (
        <div className="stack">
          <div className="card reveal" style={{ background: STATUS[res.status][1], color: "#fff", border: "none" }}>
            <h2 style={{ color: "#fff" }}>{STATUS[res.status][0]}</h2>
            <p style={{ margin: 0, fontSize: 15 }}>{res.status_meaning}</p>
            <div className="row" style={{ marginTop: 12, gap: 24 }}>
              <div><div className="small" style={{ opacity: .85 }}>Requested EMI</div><b className="num" style={{ fontSize: 22 }}>{inr(res.requested.emi)}</b></div>
              <div><div className="small" style={{ opacity: .85 }}>Estimated affordable EMI</div><b className="num" style={{ fontSize: 22 }}>{inr(res.affordable_emi)}</b></div>
              <div><div className="small" style={{ opacity: .85 }}>Disposable income</div><b className="num" style={{ fontSize: 22 }}>{inr(res.cash_flow.disposable_income)}</b></div>
            </div>
            <div className="small" style={{ marginTop: 8, opacity: .85 }}>Rule: {res.affordability_rule}</div>
          </div>
          <div className="grid g2 stagger">
            <div className="card">
              <h3>🌪️ Stress tests</h3>
              <div style={{ height: 230 }}>
                <ResponsiveContainer>
                  <BarChart data={res.stress_tests} layout="vertical" margin={{ left: 40 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f0e6d8" />
                    <XAxis type="number" tickFormatter={kfmt} /><YAxis type="category" dataKey="scenario" width={150} tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(v) => inr(v)} /><ReferenceLine x={0} stroke="#10292d" />
                    <Bar dataKey="disposable_after_emi" name="Left after EMI" radius={[0, 8, 8, 0]}>
                      {res.stress_tests.map((s) => <Cell key={s.scenario} fill={s.passes ? "#00a676" : "#ef476f"} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div className="card">
              <h3>📈 Monthly income used</h3>
              <div style={{ height: 160 }}>
                <ResponsiveContainer>
                  <LineChart data={res.cash_flow.months_used.map((m, i) => ({ m, v: res.cash_flow.monthly_income[i] }))}>
                    <XAxis dataKey="m" /><YAxis hide /><Tooltip formatter={(v) => inr(v)} />
                    <Line dataKey="v" stroke="#00a676" strokeWidth={3} dot={{ r: 5, fill: "#ffb020" }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <dl className="kv small">
                <dt>Avg recurring income</dt><dd>{inr(res.cash_flow.avg_monthly_income)}</dd>
                <dt>Essential spend</dt><dd>{inr(res.cash_flow.essential)}</dd>
                <dt>Existing obligations</dt><dd>{inr(res.cash_flow.existing_obligations)}</dd>
                <dt>Income volatility (CV)</dt><dd>{(res.cash_flow.income_volatility_cv * 100).toFixed(0)}%</dd>
              </dl>
            </div>
            <div className="card">
              <h3>👍 Supporting factors</h3>
              <ul>{res.supporting_factors.map((x) => <li key={x}>{x}</li>)}</ul>
              <h3>👎 Limiting factors</h3>
              <ul>{res.limiting_factors.map((x) => <li key={x}>{x}</li>)}</ul>
              {res.missing_information.length > 0 && <div className="notice">{res.missing_information.join(" ")}</div>}
            </div>
            <div className="card">
              <h3>💡 Illustrative options at {inr(res.affordable_emi)}/month</h3>
              <table><thead><tr><th>Tenure</th><th className="r">Max loan</th></tr></thead>
                <tbody>{res.illustrative_options.map((o) => <tr key={o.tenure_months}><td>{o.tenure_months} months</td><td className="r num"><b>{inr(o.max_principal)}</b></td></tr>)}</tbody></table>
              {res.model_signal && (
                <div className="notice info" style={{ marginTop: 10 }}>
                  <b>Model signal: {res.model_signal.band} risk</b> ({(res.model_signal.modelled_probability * 100).toFixed(1)}%)<br />
                  <span className="small">{res.model_signal.label_meaning} {res.model_signal.caveat}</span>
                </div>
              )}
            </div>
          </div>
          <div className="notice small">⚖️ {res.disclaimer}</div>
        </div>
      )}
      {consent && me.transactions > 0 && <CashflowScore />}
      {consent && me.transactions > 0 && <LenderModel principal={f.principal} months={f.months} />}
    </div>
  );
}
