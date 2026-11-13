import { useState } from "react";
import { fin } from "../api.js";
import ProfileWizard from "./ProfileWizard.jsx";

const LABELS = { analytics: "📊 Spending analytics", smart_spending: "🛍️ Smart Spending personalisation", credit: "🏦 Credit affordability" };

export default function Settings({ me, onChange }) {
  const [s, setS] = useState(me.settings);
  const [consents, setConsents] = useState(me.consents);
  const [msg, setMsg] = useState("");
  const [commit, setCommit] = useState({ name: "", amount: "", due: "" });
  const save = async (patch) => {
    await fin.put("/settings", patch);
    setMsg("✅ Saved");
    onChange?.();
    setTimeout(() => setMsg(""), 2000);
  };
  return (
    <div className="stack" style={{ maxWidth: 860 }}>
      <div className="page-head reveal"><div><h1>Settings & privacy 🛡️</h1><p className="muted">You're in control of what FinLens uses and keeps.</p></div>{msg && <span className="pill green">{msg}</span>}</div>
      <details className="card reveal" open={!me.profile?.completed}>
        <summary style={{ cursor: "pointer", fontWeight: 800 }}>🙋 Your profile: who you are, income, goals and preferences</summary>
        <div style={{ marginTop: 14 }}><ProfileWizard compact onDone={() => { setMsg("✅ Profile saved"); onChange?.(); }} /></div>
      </details>
      <div className="card stack reveal">
        <h3>Consents</h3>
        {Object.entries(me.purposes).map(([p, text]) => (
          <label key={p} className="check">
            <input type="checkbox" checked={!!consents[p]} onChange={async (e) => { setConsents(await fin.post("/consents", { purpose: p, granted: e.target.checked })); onChange?.(); }} />
            <span><b>{LABELS[p]}</b><br /><span className="muted small">{text}</span></span>
          </label>))}
      </div>
      <div className="grid g2">
        <div className="card stack reveal">
          <h3>💰 Safe-to-spend settings</h3>
          <label className="field">Emergency buffer (₹)<input type="number" min="0" value={s.emergency_buffer} onChange={(e) => setS({ ...s, emergency_buffer: +e.target.value })} /></label>
          <label className="field">Opening balance (only if your statement has no Balance column)<input type="number" value={s.opening_balance ?? ""} onChange={(e) => setS({ ...s, opening_balance: e.target.value === "" ? null : +e.target.value })} /></label>
          <label className="field">Location for restaurant search<input value={s.location || ""} onChange={(e) => setS({ ...s, location: e.target.value })} /></label>
          <button onClick={() => save({ emergency_buffer: s.emergency_buffer, opening_balance: s.opening_balance, location: s.location })}>Save</button>
        </div>
        <div className="card stack reveal">
          <h3>📌 Upcoming commitments</h3>
          <p className="muted small" style={{ margin: 0 }}>Add known payments that aren't detected automatically (e.g. exam fee). They're reserved in safe-to-spend.</p>
          {(s.manual_commitments || []).map((c, i) => (
            <div key={i} className="row between"><span>{c.name} · <b className="num">₹{c.amount}</b> <span className="muted small">{c.due}</span></span>
              <button className="small subtle" onClick={() => { const m = s.manual_commitments.filter((_, j) => j !== i); setS({ ...s, manual_commitments: m }); save({ manual_commitments: m }); }}>✕</button></div>))}
          <div className="row">
            <input placeholder="Name" value={commit.name} onChange={(e) => setCommit({ ...commit, name: e.target.value })} style={{ flex: 2 }} />
            <input placeholder="₹" type="number" value={commit.amount} onChange={(e) => setCommit({ ...commit, amount: e.target.value })} style={{ flex: 1 }} />
            <input type="date" value={commit.due} onChange={(e) => setCommit({ ...commit, due: e.target.value })} style={{ flex: 1.5 }} />
          </div>
          <button className="warm" disabled={!commit.name || !commit.amount} onClick={() => { const m = [...(s.manual_commitments || []), { ...commit, amount: +commit.amount }]; setS({ ...s, manual_commitments: m }); setCommit({ name: "", amount: "", due: "" }); save({ manual_commitments: m }); }}>Add commitment</button>
        </div>
      </div>
      <div className="card stack reveal" style={{ borderColor: "#f6c9d4" }}>
        <h3>🗑️ Your data</h3>
        <p className="muted small" style={{ margin: 0 }}>Delete statements, transactions, goals, corrections and chat sessions. Your consent history is kept as a record of what you agreed to.</p>
        <div className="row">
          <button className="danger" onClick={async () => { if (confirm("Delete all your financial data from FinLens? This cannot be undone.")) { await fin.del("/data"); onChange?.(); location.href = "/finlens"; } }}>Delete my data</button>
          <button className="subtle" onClick={async () => { if (confirm("Delete your FinLens account and all data? This cannot be undone.")) { await fin.del("/account"); fin.setToken(null); location.href = "/"; } }}>Delete account</button>
        </div>
      </div>
    </div>
  );
}
