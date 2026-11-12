import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { fin, inr } from "../api.js";
import { Blobs, Brand, CountUp } from "../ui.jsx";
import Home from "./Home.jsx";
import Transactions from "./Transactions.jsx";
import Ask from "./Ask.jsx";
import SmartSpending from "./SmartSpending.jsx";
import Planner from "./Planner.jsx";
import Credit from "./Credit.jsx";
import Settings from "./Settings.jsx";
import Onboarding from "./Onboarding.jsx";

function Login({ onDone }) {
  const [mode, setMode] = useState("login");
  const [f, setF] = useState({ name: "", email: "", password: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = mode === "login" ? await fin.login("/auth/login", { email: f.email, password: f.password }) : await fin.login("/auth/register", f);
      fin.setToken(r.token);
      onDone();
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <div className="login-wrap">
      <Blobs />
      <form className="card login stack" onSubmit={submit}>
        <Brand name="FinLens" />
        <h2>{mode === "login" ? "Welcome back 👋" : "Let's get your money clear ✨"}</h2>
        <div className="tabs">
          <button type="button" className={mode === "login" ? "on" : ""} onClick={() => setMode("login")}>Sign in</button>
          <button type="button" className={mode === "register" ? "on" : ""} onClick={() => setMode("register")}>Create account</button>
        </div>
        {mode === "register" && <label className="field">Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></label>}
        <label className="field">Email<input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required /></label>
        <label className="field">Password<input type="password" minLength={8} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required /></label>
        {err && <div className="error">{err}</div>}
        <button disabled={busy}>{busy ? "One moment…" : mode === "login" ? "Sign in" : "Create account"}</button>
        <p className="fineprint">🔒 FinLens never asks for your bank login. You upload a statement CSV yourself and can delete your data at any time.</p>
      </form>
    </div>
  );
}

const NAV = [
  ["", "🏠", "Home"], ["transactions", "🧾", "Transactions"], ["ask", "✨", "Ask FinLens"], ["spending", "🛍️", "Smart Spending"],
  ["planner", "🎯", "Planner"], ["credit", "🏦", "Credit check"], ["settings", "🛡️", "Settings & privacy"],
];

export default function FinLensApp() {
  const [authed, setAuthed] = useState(fin.hasToken());
  const [me, setMe] = useState(null);
  const [balance, setBalance] = useState(null);
  const refresh = useCallback(async () => {
    try {
      const m = await fin.get("/me");
      setMe(m);
      if (m.transactions && m.consents.analytics) {
        const d = await fin.get("/dashboard").catch(() => null);
        setBalance(d?.balance?.balance ?? null);
      } else setBalance(null);
    } catch { /* 401 handled by logout event */ }
  }, []);
  useEffect(() => {
    const h = () => { setAuthed(false); setMe(null); };
    window.addEventListener("finlens_token:logout", h);
    return () => window.removeEventListener("finlens_token:logout", h);
  }, []);
  useEffect(() => { if (authed) refresh(); }, [authed, refresh]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  if (!me) return <div className="main"><Blobs /><div className="skeleton" /></div>;
  const ready = me.transactions > 0 && me.consents.analytics;

  return (
    <div className="shell">
      <Blobs />
      <aside className="sidebar">
        <Brand name="FinLens" />
        <nav className="nav">
          {NAV.map(([p, ico, label]) => (
            <NavLink key={p} to={`/finlens/${p}`} end={p === ""}><span className="ico">{ico}</span>{label}</NavLink>
          ))}
        </nav>
        <div className="spacer" />
        {balance !== null && (
          <div className="balance-card">
            <div className="label">Your balance</div>
            <div className="val"><CountUp value={balance} format={inr} /></div>
            <div className="label">from your latest statement</div>
          </div>
        )}
        <button className="subtle small" style={{ marginTop: 10 }} onClick={() => { fin.setToken(null); setAuthed(false); }}>Sign out · {me.name}</button>
      </aside>
      <main className="main">
        <Routes>
          <Route index element={ready ? <Home me={me} /> : <Onboarding me={me} onReady={refresh} />} />
          <Route path="onboarding" element={<Onboarding me={me} onReady={refresh} />} />
          <Route path="transactions" element={ready ? <Transactions onChange={refresh} /> : <Navigate to="/finlens" />} />
          <Route path="ask" element={ready ? <Ask /> : <Navigate to="/finlens" />} />
          <Route path="spending" element={<SmartSpending me={me} />} />
          <Route path="planner" element={ready ? <Planner /> : <Navigate to="/finlens" />} />
          <Route path="credit" element={<Credit me={me} onConsent={refresh} />} />
          <Route path="settings" element={<Settings me={me} onChange={refresh} />} />
        </Routes>
      </main>
    </div>
  );
}
