import { useEffect, useState } from "react";
import { fin } from "../api.js";

const STAGE = {
  new: ["🌱 Starting from zero", "linear-gradient(135deg,#00a676,#1e9bf0)"],
  thin: ["🌿 Young credit file", "linear-gradient(135deg,#1e9bf0,#13b5b1)"],
  building: ["🌳 Building nicely", "linear-gradient(135deg,#ff9233,#ffb020)"],
  established: ["🏆 Established", "linear-gradient(135deg,#7cc243,#00a676)"],
};
const BAND = { likely: "#00a676", borderline: "#ffb020", unlikely: "#ff6b4a" };

export default function CreditBuilder() {
  const [p, setP] = useState(null);
  const [err, setErr] = useState("");
  const load = () => fin.get("/credit/builder").then(setP).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  if (err) return <div className="error">{err}</div>;
  if (!p) return null;
  const [label, grad] = STAGE[p.stage] || STAGE.new;
  const toggle = async (id) => {
    setP({ ...p, steps: p.steps.map((s) => (s.id === id ? { ...s, done: !s.done } : s)) });
    await fin.post(`/credit/builder/${id}/toggle`).catch(() => {});
    load();
  };
  const pct = Math.round((p.progress.done / Math.max(1, p.progress.total)) * 100);
  const months = [...new Set(p.steps.map((s) => s.month))].sort((a, b) => a - b);
  return (
    <div className="stack reveal">
      <div className="card" style={{ background: grad, color: "#fff", border: "none" }}>
        <div className="row between" style={{ flexWrap: "wrap", gap: 12 }}>
          <div style={{ maxWidth: 680 }}>
            <div className="small" style={{ opacity: .9, textTransform: "uppercase", fontWeight: 700, letterSpacing: .4 }}>Credit-builder path</div>
            <h2 style={{ color: "#fff", margin: "4px 0" }}>{label}</h2>
            <p style={{ margin: 0 }}>{p.stage_text}</p>
          </div>
          <div style={{ textAlign: "center" }}>
            <div className="ring" style={{ background: `conic-gradient(#fff ${pct * 3.6}deg, rgba(255,255,255,.25) 0)` }}><span>{pct}%</span></div>
            <div className="small" style={{ marginTop: 4 }}>{p.progress.done}/{p.progress.total} steps done</div>
          </div>
        </div>
      </div>

      {p.timeline.length > 0 && (
        <div className="card lift">
          <h3>📈 How your approval odds grow</h3>
          <p className="small muted" style={{ marginTop: -4 }}>Built-in lender model, for a {p.timeline_loan}, if you follow the path with no missed payments.</p>
          <div className="grid" style={{ gridTemplateColumns: `repeat(${p.timeline.length}, 1fr)`, gap: 10, alignItems: "end" }}>
            {p.timeline.map((t, i) => (
              <div key={t.month} style={{ textAlign: "center" }}>
                <div className="num" style={{ fontWeight: 800, color: BAND[t.band] }}>{Math.round(t.approval * 100)}%</div>
                <div className="tl-bar" style={{ height: `${Math.max(8, t.approval * 140)}px`, background: BAND[t.band], animationDelay: `${i * 0.12}s` }} />
                <b className="small">{t.label}</b>
                <div className="small muted">{t.credit_score ? `score ${t.credit_score}` : "no score yet"} · {t.credit_history_years} yrs history</div>
              </div>))}
          </div>
          <ul className="small muted" style={{ paddingLeft: 18, marginBottom: 0 }}>{p.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </div>)}
      {p.model_note && <div className="notice small">{p.model_note}</div>}

      <div className="card lift">
        <h3>🗺️ Your steps</h3>
        {months.map((m) => (
          <div key={m} style={{ marginBottom: 6 }}>
            <div className="pill blue" style={{ margin: "8px 0" }}>{m === 0 ? "This week" : m === 1 ? "Month 1" : `Month ${m}`}</div>
            {p.steps.filter((s) => s.month === m).map((s) => (
              <label key={s.id} className={`step-row ${s.done ? "done" : ""}`}>
                <input type="checkbox" checked={s.done} onChange={() => toggle(s.id)} />
                <div>
                  <b>{s.icon} {s.title}</b>{s.optional && <span className="pill" style={{ marginLeft: 6 }}>optional</span>}
                  <div className="small">{s.why}</div>
                  <div className="small muted" style={{ marginTop: 2 }}>👉 {s.how}</div>
                </div>
              </label>))}
          </div>))}
        <p className="small muted" style={{ marginBottom: 0 }}>{p.caveat}</p>
      </div>
    </div>
  );
}
