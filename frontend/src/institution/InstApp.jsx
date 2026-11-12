import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { inst } from "../api.js";
import { Blobs, Brand } from "../ui.jsx";
import Overview from "./Overview.jsx";
import Alerts from "./Alerts.jsx";
import AlertDetail from "./AlertDetail.jsx";
import Network from "./Network.jsx";
import AccountView from "./AccountView.jsx";
import Audit from "./Audit.jsx";
import Model from "./Model.jsx";

function Login({ onDone }) {
  const [f, setF] = useState({ username: "", password: "" });
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault(); setErr("");
    try { const r = await inst.login("/auth/login", f); inst.setToken(r.token); onDone(); } catch (x) { setErr(x.message); }
  };
  return (
    <div className="inst login-wrap">
      <Blobs />
      <form className="card login stack" onSubmit={submit}>
        <Brand name="MuleScope" />
        <h2>Investigator sign-in 🕵️</h2>
        <p className="muted small" style={{ margin: 0 }}>Institutional portal: separate accounts, data store and keys from FinLens.</p>
        <label className="field">Username<input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required /></label>
        <label className="field">Password<input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required /></label>
        {err && <div className="error">{err}</div>}
        <button>Sign in</button>
        <p className="fineprint">Local demo users are seeded from <code>.env</code> (see README). All access is audit-logged.</p>
      </form>
    </div>
  );
}

const NAV = [["", "📊", "Overview"], ["alerts", "🚨", "Alerts"], ["network", "🕸️", "Network"], ["model", "🧪", "Model & validation"], ["audit", "📜", "Audit log"]];

export default function InstApp() {
  const [authed, setAuthed] = useState(inst.hasToken());
  const [me, setMe] = useState(null);
  const [dsId, setDsId] = useState(() => Number(localStorage.getItem("inst_ds")) || null);
  const nav = useNavigate();
  useEffect(() => {
    const h = () => { setAuthed(false); setMe(null); };
    window.addEventListener("institution_token:logout", h);
    return () => window.removeEventListener("institution_token:logout", h);
  }, []);
  useEffect(() => { if (authed) inst.get("/me").then(setMe).catch(() => {}); }, [authed]);
  // default to the most recent dataset (or drop a stale id from another session)
  useEffect(() => {
    if (!authed) return;
    inst.get("/datasets").then((l) => {
      if (l.length && (!dsId || !l.some((d) => d.id === dsId))) { setDsId(l[0].id); localStorage.setItem("inst_ds", l[0].id); }
    }).catch(() => {});
  }, [authed]); // eslint-disable-line react-hooks/exhaustive-deps
  const chooseDs = (id) => { setDsId(id); localStorage.setItem("inst_ds", id); };
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  if (!me) return <div className="inst main"><div className="skeleton" /></div>;
  return (
    <div className="inst shell">
      <Blobs />
      <aside className="sidebar">
        <Brand name="MuleScope" />
        <nav className="nav">{NAV.map(([p, ico, l]) => <NavLink key={p} to={`/institution/${p}`} end={p === ""}><span className="ico">{ico}</span>{l}</NavLink>)}</nav>
        <div className="spacer" />
        <div className="balance-card">
          <div className="label">Signed in as</div>
          <div className="val" style={{ fontSize: 18 }}>{me.name}</div>
          <div className="label">role: <b>{me.role}</b>{dsId ? ` · dataset #${dsId}` : ""}</div>
        </div>
        <button className="subtle small" style={{ marginTop: 10 }} onClick={() => { inst.setToken(null); setAuthed(false); nav("/institution"); }}>Sign out</button>
      </aside>
      <main className="main">
        <Routes>
          <Route index element={<Overview dsId={dsId} setDsId={chooseDs} me={me} />} />
          <Route path="alerts" element={<Alerts dsId={dsId} />} />
          <Route path="alerts/:id" element={<AlertDetail me={me} />} />
          <Route path="network" element={<Network dsId={dsId} />} />
          <Route path="accounts/:ds/:alias" element={<AccountView me={me} />} />
          <Route path="model" element={<Model />} />
          <Route path="audit" element={<Audit me={me} />} />
        </Routes>
      </main>
    </div>
  );
}
