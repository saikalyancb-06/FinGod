import { useEffect, useState } from "react";
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
          <div className="stat"><div className="label">Projected score</div><div className="value" style={{ color: delta > 0 ? "var(--emerald)" : "var(--ink)" }}><CountUp value={wi.score} format={(v) => Math.round(v)} duration={500} /> <span className="small">{delta > 0 ? `▲ ${delta}` : ""}</span></div></div>
        </div>
      </div>
      <p className="small muted" style={{ marginBottom: 0 }}>{cur.method}. {cur.source}</p>
    </div>
  );
}

export function LenderModel({ principal, months }) {
  const [f, setF] = useState({ no_of_dependents: 0, education: "Graduate", self_employed: "No", residential_assets: 0, commercial_assets: 0, luxury_assets: 0, bank_assets: 0 });
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => { setBusy(true); try { setR(await fin.post("/credit/lender-model", { ...f, principal: +principal, months: +months })); } finally { setBusy(false); } };
  const num = (k, label) => <label className="field" key={k}>{label}<input type="number" min="0" value={f[k]} onChange={(e) => setF({ ...f, [k]: +e.target.value })} /></label>;
  return (
    <div className="card reveal stack">
      <div className="row between"><h3><span className="chip-ico" style={{ background: "var(--saffron-bg)" }}>🏛️</span>Existing lender-style model (CredWise API)</h3><span className="pill amber">side signal only</span></div>
      <p className="small muted" style={{ margin: 0 }}>Sends the CredWise loan-approval fields to the team's existing model API. Per the blueprint's model audit, that model's label is <b>historical approval</b>, not repayment, so it never changes the FinLens affordability status above.</p>
      <div className="grid g4" style={{ gap: 10 }}>
        {num("no_of_dependents", "Dependents")}
        <label className="field">Education<select value={f.education} onChange={(e) => setF({ ...f, education: e.target.value })}><option>Graduate</option><option>Not Graduate</option></select></label>
        <label className="field">Self-employed<select value={f.self_employed} onChange={(e) => setF({ ...f, self_employed: e.target.value })}><option>No</option><option>Yes</option></select></label>
        {num("bank_assets", "Bank assets ₹")}
        {num("residential_assets", "Residential assets ₹")}{num("commercial_assets", "Commercial assets ₹")}{num("luxury_assets", "Luxury assets ₹")}
      </div>
      <div><button className="subtle" disabled={busy} onClick={run}>{busy ? "Calling…" : "Run lender-style model"}</button></div>
      {r && (
        <div className={r.configured && !r.error ? "notice info" : "notice"}>
          {!r.configured && <div>⚙️ {r.message}</div>}
          {r.error && <div>⚠️ {r.error}</div>}
          {r.response && <pre className="small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify(r.response, null, 2)}</pre>}
          <div className="small" style={{ marginTop: 6 }}><b>Caveat:</b> {r.caveat}</div>
          <details className="small" style={{ marginTop: 6 }}><summary>Payload sent</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(r.payload, null, 2)}</pre></details>
        </div>)}
    </div>
  );
}
