import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { fin, inr } from "../api.js";
import { CountUp, Skeleton } from "../ui.jsx";
import { SpeakButton } from "../voice.jsx";
import { useGuard } from "./GuardContext.jsx";

const NAT = { need: ["Need", "green"], flex: ["Depends", "blue"], want: ["Want", "red"], charge: ["Fee", "amber"] };
const COLOR = { green: "#00a676", amber: "#ffb020", red: "#ef476f", grey: "#9aa9ad" };
const TABS = [["today", "📅 Today"], ["live", "📲 Live input"], ["alerts", "🔔 Alerts"], ["insights", "🔍 Insights"],
  ["wishlist", "🧘 Wishlist"], ["plan", "🗓️ Plan"], ["mine", "🎛️ Make it mine"]];

export function NaturePill({ n, reason }) {
  if (!n) return null;
  const [label, cls] = NAT[n] || NAT.flex;
  return <span className={`pill ${cls}`} title={reason || ""}>{label}</span>;
}

function Ring({ pct, color, children, size = 150 }) {
  const p = Math.min(100, pct || 0);
  return (
    <div className="guard-ring" style={{ width: size, height: size, background: `conic-gradient(${COLOR[color] || COLOR.grey} ${p * 3.6}deg, #f1ebe2 0)` }}>
      <div className="guard-ring-in">{children}</div>
    </div>
  );
}

// ---------------- Today ----------------
function Today({ version }) {
  const [day, setDay] = useState(null);
  const [d, setD] = useState(null);
  const [goals, setGoals] = useState([]);
  const load = () => fin.get("/guard/today" + (day ? `?day=${day}` : "")).then(setD).catch(() => setD({ empty: true }));
  useEffect(() => { load(); }, [day, version]); // eslint-disable-line
  useEffect(() => { fin.get("/goals").then((g) => setGoals(g.goals)).catch(() => {}); }, [version]);
  if (!d) return <Skeleton rows={3} />;
  if (d.empty) return <div className="notice">Upload a statement or use <b>Live input</b> to start.</div>;
  const dl = d.daily, ex = d.explain;
  const shift = (n) => { const x = new Date((day || dl.date) + "T00:00"); x.setDate(x.getDate() + n); setDay(x.toISOString().slice(0, 10)); };
  return (
    <div className="stack">
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.4fr)", gap: 16 }}>
        <div className="card lift" style={{ textAlign: "center" }}>
          <div className="row between"><button className="small ghost" onClick={() => shift(-1)}>◀</button>
            <b>{new Date(dl.date + "T00:00").toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short" })}</b>
            <button className="small ghost" onClick={() => shift(1)} disabled={!day}>▶</button></div>
          <div style={{ display: "grid", placeItems: "center", margin: "14px 0" }}>
            <Ring pct={dl.pct} color={dl.color}>
              <div className="small muted">spent today</div>
              <div className="num" style={{ fontSize: 26, fontWeight: 800 }}><CountUp value={dl.spent} format={inr} /></div>
              {dl.limit != null && <div className="small muted">of {inr(dl.limit)}</div>}
            </Ring>
          </div>
          <div className={`pill ${dl.color === "red" ? "red" : dl.color === "amber" ? "amber" : "green"}`} style={{ fontSize: 13 }}>
            {dl.color === "green" ? "🟢 Comfortable" : dl.color === "amber" ? "🟠 Getting close" : dl.color === "red" ? "🔴 Over today's amount" : "No limit yet"}
            {dl.left != null && dl.left > 0 ? ` · ${inr(dl.left)} left` : ""}</div>
          <p className="small muted">Counts wants and "depends" spends only, not needs or bills. Based on {dl.basis}.{dl.rollover ? " Unspent money rolls over." : ""}</p>
          <div className="row" style={{ justifyContent: "center", gap: 6 }}>
            {Object.entries(dl.by_nature || {}).map(([k, v]) => <span key={k} className={`pill ${(NAT[k] || NAT.flex)[1]}`}>{(NAT[k] || NAT.flex)[0]} {inr(v)}</span>)}
          </div>
        </div>
        <div className="card lift">
          <div className="row between"><h3 style={{ margin: 0 }}>🧾 Where did my money go?</h3>{!ex.empty && <SpeakButton text={ex.speech} />}</div>
          {ex.empty ? <p className="muted">{ex.speech}</p> : (<>
            <p style={{ margin: "8px 0" }}>{ex.speech}</p>
            {ex.start_balance != null && ex.end_balance != null && (
              <div className="bal-drop"><span>{inr(ex.start_balance)}</span><i style={{ width: `${Math.max(4, Math.min(100, ex.end_balance / Math.max(1, ex.start_balance) * 100))}%` }} /><span>{inr(ex.end_balance)}</span></div>)}
            <div className="day-list">
              {ex.items.map((it) => (
                <div key={it.id} className="row between day-row">
                  <span className="small muted num" style={{ width: 44 }}>{it.time || ""}</span>
                  <span style={{ flex: 1, minWidth: 0 }}><b>{it.merchant}</b> <NaturePill n={it.nature} reason={it.reason} />{it.reason && it.nature !== "need" && <div className="small muted">{it.reason}</div>}</span>
                  <b className="num" style={{ color: it.type === "credit" ? "var(--emerald)" : "var(--ink)" }}>{it.type === "credit" ? "+" : "−"}{inr(it.amount)}</b>
                </div>))}
            </div></>)}
        </div>
      </div>
      <div className="grid g3 stagger">
        <div className={`card lift ${d.shield.at_risk ? "warn" : ""}`}>
          <h3>🛡️ Bill shield</h3>
          {d.shield.known ? (<>
            <p style={{ margin: 0 }}>{d.shield.text}</p>
            {d.shield.bills.map((b, i) => <div key={i} className="row between small" style={{ marginTop: 6 }}><span>{b.merchant} · {b.due}</span><b className="num">{inr(b.amount)}</b></div>)}
          </>) : <p className="muted small">Needs your balance (statement balance column or opening balance in Settings).</p>}
        </div>
        <div className="card lift">
          <h3>🫙 Savings jar</h3>
          <div className="num" style={{ fontSize: 26, fontWeight: 800, color: "var(--emerald)" }}>{inr(d.jar.pending)}</div>
          <p className="small muted" style={{ marginTop: 2 }}>Want tax {inr(d.jar.by_kind.want_tax)} · round-ups {inr(d.jar.by_kind.roundup)} · {inr(d.jar.moved)} moved so far</p>
          {d.jar.pending > 0 && <JarMove goals={goals} onDone={load} />}
        </div>
        <div className="card lift">
          <h3>🪞 Worth it?</h3>
          {d.reflect.length ? d.reflect.map((r) => <Reflect key={r.txn_id} r={r} onDone={load} />) : <p className="muted small">Nothing to rate. Wants you buy show up here so FinLens learns what you value.</p>}
        </div>
      </div>
      {d.caps.length > 0 && (
        <div className="card lift">
          <h3>🎚️ Your caps this month</h3>
          {d.caps.map((c) => (
            <div key={c.key} style={{ marginBottom: 10 }}>
              <div className="row between small"><b>{c.key}</b><span className="num">{inr(c.spent)} / {inr(c.cap)}</span></div>
              <div className="progress" style={{ height: 8 }}><i style={{ width: `${Math.min(100, c.pct)}%`, background: c.pct >= 100 ? "var(--rose)" : c.pct >= 80 ? "var(--saffron)" : "var(--emerald)" }} /></div>
            </div>))}
        </div>)}
      {d.wants_month?.top_wants?.length > 0 && (
        <div className="card lift">
          <h3>🎯 Wants this month: {d.wants_month.want_share_pct}% of spending</h3>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>{d.wants_month.top_wants.map((w) => <span key={w.merchant} className="pill red">{w.merchant} {inr(w.amount)}</span>)}</div>
        </div>)}
    </div>
  );
}

function JarMove({ goals, onDone }) {
  const [g, setG] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <div className="stack" style={{ gap: 6 }}>
      <select value={g} onChange={(e) => setG(e.target.value)}><option value="">To my main goal</option>{goals.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
      <div className="row" style={{ gap: 6 }}>
        <button className="small" onClick={async () => { const r = await fin.post("/guard/jar/move", { goal_id: g ? +g : null }); setMsg(`✓ ${inr(r.moved)} added${r.goal ? ` to ${r.goal}` : ""}`); onDone(); }}>I moved it to savings</button>
        <button className="small ghost" onClick={async () => { await fin.post("/guard/jar/skip"); onDone(); }}>Skip</button>
      </div>
      <span className="small muted">FinLens can't move money; move it in your bank/UPI app, then tap.</span>
      {msg && <span className="small" style={{ color: "var(--emerald)" }}>{msg}</span>}
    </div>
  );
}

function Reflect({ r, onDone }) {
  const [note, setNote] = useState("");
  const send = async (v) => { const x = await fin.post("/guard/reflect", { txn_id: r.txn_id, verdict: v }); setNote(x.note); setTimeout(onDone, 1800); };
  return (
    <div className="reflect-row">
      <div className="small"><b>{r.merchant}</b> · {inr(r.amount)} · {r.date}</div>
      {note ? <div className="small" style={{ color: "var(--emerald)" }}>{note}</div> :
        <div className="row" style={{ gap: 6, marginTop: 4 }}><button className="small" onClick={() => send("worth")}>👍 Worth it</button><button className="small ghost" onClick={() => send("regret")}>😕 Regret</button></div>}
    </div>
  );
}

// ---------------- Live input ----------------
const SAMPLE_SMS = `Rs.1,299.00 debited from A/c XX1234 on 13-11-26 22:41 to VPA boat.lifestyle@ybl (UPI Ref No 512345678). Avl Bal Rs 2,401.00 -SBI

INR 245.00 spent on HDFC Bank Card XX1234 at ZOMATO on 2026-11-13:21:10:05. Avl Lmt: INR 45,000

RAHUL has requested money from you on Google Pay. Rs 3,000 will be debited from your account on approval.`;

function Live() {
  const g = useGuard();
  const [text, setText] = useState("");
  const [res, setRes] = useState(null);
  const [q, setQ] = useState({ amount: "", what: "", type: "debit" });
  const [chk, setChk] = useState({ item: "", price: "" });
  const [verdict, setVerdict] = useState(null);
  const [speed, setSpeed] = useState(5);
  const [perm, setPerm] = useState(typeof Notification !== "undefined" ? Notification.permission : "unsupported");
  const d = g.demo;
  const paste = async (e) => { e.preventDefault(); const r = await fin.post("/guard/sms", { text }); setRes(r.results); g.ingested(r.results); setText(""); };
  const quick = async (e) => { e.preventDefault(); const r = await fin.post("/guard/quick", { amount: +q.amount, what: q.what, type: q.type }); setRes(r.results); g.ingested(r.results); setQ({ amount: "", what: "", type: "debit" }); };
  const check = async (e) => { e.preventDefault(); setVerdict({ ...(await fin.post("/guard/check", { item: chk.item, price: +chk.price })), item: chk.item, price: +chk.price }); };
  return (
    <div className="stack">
      <div className="card lift" style={{ borderTop: "4px solid var(--coral)" }}>
        <div className="row between" style={{ flexWrap: "wrap", gap: 10 }}>
          <div><h3 style={{ margin: 0 }}>▶ Demo: a live day of spending</h3>
            <p className="small muted" style={{ margin: "4px 0 0" }}>12 realistic bank SMS for <b>your</b> account (metro, chai, a fee, sunglasses charged twice, fantasy sports, food delivery, a collect-request scam, a late-night unknown merchant). Each one goes through the same SMS reader and every check, live.</p></div>
          <div className="row" style={{ gap: 6 }}>
            {!d.running ? <button onClick={() => d.start(speed)} disabled={d.step >= d.total}>{d.step ? "▶ Continue" : "▶ Start the day"}</button> : <button className="warm" onClick={d.stop}>⏸ Pause</button>}
            <button className="small ghost" onClick={() => d.next()} disabled={d.running || d.step >= d.total}>Next SMS</button>
            <button className="small subtle" onClick={d.reset}>↺ Reset demo</button>
          </div>
        </div>
        <div className="row" style={{ gap: 10, marginTop: 10 }}>
          <span className="small muted">One SMS every</span><input type="range" min="2" max="12" value={speed} onChange={(e) => setSpeed(+e.target.value)} style={{ width: 140, padding: 0, border: "none", accentColor: "var(--coral)" }} /><b className="small">{speed}s</b>
          <div className="progress" style={{ flex: 1, height: 8 }}><i style={{ width: `${(d.step / d.total) * 100}%`, background: "linear-gradient(90deg,var(--coral),var(--saffron))" }} /></div>
          <span className="small num">{d.step}/{d.total}</span>
        </div>
        {d.last && <div className="sms-bubble"><div className="small muted">📩 SBI · just now</div>{d.last.sms}</div>}
      </div>
      <div className="grid g2">
        <form className="card lift stack" onSubmit={paste}>
          <h3 style={{ margin: 0 }}>📩 Paste bank SMS</h3>
          <p className="small muted" style={{ margin: 0 }}>Copy one or many transaction SMS from your phone. FinLens reads amount, merchant, balance and time. OTPs are ignored; payment requests are flagged as possible scams. No bank login, ever.</p>
          <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder="Rs.450.00 debited from A/c XX1234 on 13-11-26 21:14 to VPA swiggy@icici…" />
          <div className="row" style={{ gap: 6 }}><button disabled={!text.trim()}>Read SMS</button><button type="button" className="small ghost" onClick={() => setText(SAMPLE_SMS)}>Try a sample</button></div>
        </form>
        <div className="stack">
          <form className="card lift stack" onSubmit={check} style={{ borderTop: "4px solid var(--sky)" }}>
            <h3 style={{ margin: 0 }}>🤔 Before you pay</h3>
            <p className="small muted" style={{ margin: 0 }}>About to buy something? Check it first.</p>
            <div className="row" style={{ gap: 6 }}><input placeholder="e.g. Ray-Ban sunglasses" value={chk.item} onChange={(e) => setChk({ ...chk, item: e.target.value })} required style={{ flex: 2 }} />
              <input type="number" min="1" placeholder="₹ price" value={chk.price} onChange={(e) => setChk({ ...chk, price: e.target.value })} required style={{ flex: 1 }} /><button>Check</button></div>
            {verdict && <Verdict v={verdict} />}
          </form>
          <form className="card lift stack" onSubmit={quick}>
            <h3 style={{ margin: 0 }}>✍️ Quick add (cash, or anything the SMS missed)</h3>
            <div className="row" style={{ gap: 6 }}>
              <input type="number" min="1" placeholder="₹" value={q.amount} onChange={(e) => setQ({ ...q, amount: e.target.value })} required style={{ width: 100 }} />
              <input placeholder="what / where (e.g. pani puri)" value={q.what} onChange={(e) => setQ({ ...q, what: e.target.value })} required style={{ flex: 1 }} />
              <select value={q.type} onChange={(e) => setQ({ ...q, type: e.target.value })} style={{ width: 110 }}><option value="debit">spent</option><option value="credit">received</option></select>
              <button>Add</button>
            </div>
          </form>
        </div>
      </div>
      {res && (
        <div className="card lift">
          <h3>Result</h3>
          {res.map((x, i) => (
            <div key={i} className="row between day-row">
              <span><b>{x.merchant || x.status}</b> {x.nature && <NaturePill n={x.nature} reason={x.reason} />} <span className="small muted">{x.reason || x.text || ""}</span></span>
              <span className="small">{x.amount != null && <b className="num">{inr(x.amount)}</b>} {(x.alerts || []).map((a) => <span key={a.id} className={`pill ${a.severity === "alert" ? "red" : a.severity === "warn" ? "amber" : "blue"}`}>{a.kind}</span>)}</span>
            </div>))}
        </div>)}
      <div className="notice small">🔔 Alerts pop up on any FinLens page. {perm === "granted" ? "Browser notifications are on (outside your quiet hours)." :
        perm === "unsupported" ? "This browser doesn't support notifications." :
          <button className="small" onClick={async () => setPerm(await Notification.requestPermission())}>Allow browser notifications</button>}
        <span className="muted"> In a real app the same checks would run on every bank SMS (Android) or Account Aggregator update.</span></div>
    </div>
  );
}

export function Verdict({ v, compact }) {
  const [added, setAdded] = useState(false);
  const color = { go: "var(--emerald)", ok_treat: "var(--sky)", wait: "var(--saffron)", stop: "var(--rose)" }[v.verdict];
  return (
    <div className="verdict" style={{ borderLeft: `5px solid ${color}` }}>
      <b style={{ fontSize: 16 }}>{v.title}</b>
      <ul className="small" style={{ margin: "6px 0", paddingLeft: 18 }}>{v.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      {v.lenses?.length > 0 && <div className="small">💭 That's {v.lenses.join(" · ")}.</div>}
      {!compact && ["wait", "stop", "ok_treat"].includes(v.verdict) && (added ? <div className="small" style={{ color: "var(--emerald)", marginTop: 6 }}>✓ Added to your wishlist. FinLens will ask again in {v.cooloff_hours} hours.</div> :
        <button className="small" style={{ marginTop: 8 }} onClick={async () => { await fin.post("/guard/wishlist", { name: v.item, price: v.price }); setAdded(true); }}>🧘 Add to wishlist and decide in {v.cooloff_hours}h</button>)}
    </div>
  );
}

// ---------------- Alerts ----------------
function Alerts({ version }) {
  const g = useGuard();
  const [rows, setRows] = useState(null);
  const [kind, setKind] = useState("");
  const [result, setResult] = useState({});
  const load = () => fin.get("/guard/alerts?limit=100").then((r) => setRows(r.alerts));
  useEffect(() => { load(); fin.post("/guard/alerts/read-all").then(g.clearUnread).catch(() => {}); }, [version]); // eslint-disable-line
  if (!rows) return <Skeleton rows={3} />;
  const shown = rows.filter((a) => !kind || a.kind === kind);
  const act = async (a, action) => { const r = await fin.post(`/guard/alerts/${a.id}`, { action }); setResult({ ...result, [a.id]: r }); load(); };
  return (
    <div className="stack">
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {["", "want", "fraud", "duplicate", "scam", "limit", "cap", "shield", "charge", "check"].map((k) => (
          <button key={k} className="small" onClick={() => setKind(k)} style={kind === k ? {} : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }}>{k || "all"}</button>))}
      </div>
      {!shown.length && <div className="card muted">No alerts yet. Try <b>Live input → Start the day</b>.</div>}
      {shown.map((a) => (
        <div key={a.id} className={`card alert-card sev-${a.severity}`}>
          <div className="row between" style={{ alignItems: "flex-start" }}>
            <div><b>{a.title}</b> {a.quiet && <span className="pill">🌙 quiet hours</span>} <span className="pill">{a.status}</span>
              <div className="small" style={{ marginTop: 4 }}>{a.body}</div>
              {a.data?.lenses?.length > 0 && <div className="small muted">💭 {a.data.lenses.join(" · ")}</div>}
              <div className="small muted">{new Date(a.created_at + "Z").toLocaleString("en-IN")}{a.data?.txn ? ` · ${a.data.txn.merchant} ${inr(a.data.txn.amount)}` : ""}</div>
              {result[a.id]?.steps && <ol className="small" style={{ paddingLeft: 18 }}>{result[a.id].steps.map((s) => <li key={s}>{s}</li>)}</ol>}
              {result[a.id]?.rule && <div className="small" style={{ color: "var(--emerald)" }}>✓ {result[a.id].rule}</div>}
            </div>
            <div className="row" style={{ gap: 4, flexWrap: "wrap", justifyContent: "flex-end" }}>
              {(a.data?.actions || []).map((x) => <button key={x} className="small ghost" onClick={() => act(a, x)}>{{ need: "It's a need", want: "It's a want", regret: "😕 Regret", worth: "👍 Worth it", confirm: "✅ It was me", dispute: "🚨 Not me" }[x] || x}</button>)}
            </div>
          </div>
        </div>))}
    </div>
  );
}

// ---------------- Insights ----------------
function Insights({ version }) {
  const [d, setD] = useState(null);
  const load = () => fin.get("/guard/insights").then(setD);
  useEffect(() => { load(); }, [version]); // eslint-disable-line
  if (!d) return <Skeleton rows={4} />;
  if (d.empty) return <div className="notice">Add a statement or live transactions first.</div>;
  const sub = async (k, v) => { await fin.post(`/guard/subscriptions/${encodeURIComponent(k)}`, { decision: v }); load(); };
  return (
    <div className="stack">
      <div className="card lift">
        <h3>🏅 Streaks & badges</h3>
        <div className="row" style={{ gap: 18, marginBottom: 10, flexWrap: "wrap" }}>
          <span>🌱 Want-free days in a row: <b>{d.streaks.no_want?.current}</b> (best {d.streaks.no_want?.best})</span>
          <span>🚦 Under your daily limit: <b>{d.streaks.under_limit?.current}</b> (best {d.streaks.under_limit?.best})</span>
        </div>
        <div className="badges">{d.streaks.badges.map((b) => <div key={b.id} className={`badge ${b.earned ? "on" : ""}`} title={b.progress}><span>{b.icon}</span><b>{b.title}</b><small>{b.earned ? "earned" : b.progress}</small></div>)}</div>
      </div>
      <div className="grid g2">
        <div className="card lift"><h3>🧠 Your spending habits</h3>
          {d.habits.length ? d.habits.map((h) => <div key={h.id} className="insight-row"><b>{h.icon} {h.title}</b><div className="small muted">{h.body}</div></div>) : <p className="muted small">Not enough wants yet to see patterns. 🙂</p>}</div>
        <div className="card lift"><h3>💧 Small leaks</h3>
          {d.leaks.length ? d.leaks.map((l) => (
            <div key={l.merchant} className="insight-row"><div className="row between"><b>{l.merchant}</b><b className="num">{inr(l.monthly)}/mo</b></div>
              <div className="small muted">{l.count} payments of about {inr(l.avg)} in 2 months · {inr(l.yearly)} a year. Halving it saves {inr(l.half_saves)}/yr{l.goal_days ? ` (${l.goal_days} days of goal saving)` : ""}.</div></div>)) : <p className="muted small">No small frequent payments adding up. Nice.</p>}</div>
        <div className="card lift"><h3>🧾 Hidden charges ({d.charges.days} days)</h3>
          <div className="num" style={{ fontSize: 24, fontWeight: 800, color: d.charges.total ? "var(--rose)" : "var(--emerald)" }}>{inr(d.charges.total)}</div>
          {d.charges.items.map((c, i) => <div key={i} className="insight-row"><div className="row between small"><span>{c.description}</span><b>{inr(c.amount)}</b></div><div className="small muted">{c.tip}</div></div>)}
          {!d.charges.items.length && <p className="muted small">No bank fees found.</p>}</div>
        <div className="card lift"><h3>📺 Subscriptions · {inr(d.subscriptions.monthly_total)}/mo</h3>
          {d.subscriptions.items.map((s) => (
            <div key={s.merchant_key} className="row between insight-row">
              <span><b>{s.merchant}</b> <span className="small muted">{inr(s.monthly)}/mo · {inr(s.yearly)}/yr · next {s.next}</span></span>
              <span className="row" style={{ gap: 4 }}>
                <button className="small" style={s.decision === "keep" ? {} : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }} onClick={() => sub(s.merchant_key, s.decision === "keep" ? "" : "keep")}>Keep</button>
                <button className="small" style={s.decision === "cancel" ? { background: "var(--rose)" } : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }} onClick={() => sub(s.merchant_key, s.decision === "cancel" ? "" : "cancel")}>Cancel</button>
              </span></div>))}
          {d.subscriptions.duplicates.map((x) => <div key={x.group} className="notice small">{x.text}</div>)}
          {d.subscriptions.trials.map((t, i) => <div key={i} className="notice small">🆓 {t.merchant} · {t.date}: {t.note}</div>)}
          {d.subscriptions.cancel_saves_yearly > 0 && <p className="small" style={{ color: "var(--emerald)" }}>Cancelling the marked ones saves {inr(d.subscriptions.cancel_saves_yearly)} a year. (Cancel in each app; FinLens can't cancel for you.)</p>}
          {!d.subscriptions.items.length && <p className="muted small">No subscriptions detected.</p>}</div>
        <div className="card lift"><h3>💡 Smarter ways</h3>
          {d.tips.length ? d.tips.map((t) => <div key={t.id} className="insight-row"><b>{t.icon} {t.title}</b><div className="small muted">{t.body}</div>{t.saves_monthly > 0 && <span className="pill green">≈ {inr(t.saves_monthly)}/month (estimate)</span>}</div>) : <p className="muted small">No obvious savings tips right now.</p>}</div>
        <div className="card lift"><h3>🤝 Money friends owe you</h3>
          {d.owed.length ? d.owed.map((o) => (
            <div key={o.friend_id} className="row between insight-row"><span><b>{o.name}</b> <span className="small muted">{inr(o.amount)} · {o.days} days</span></span>
              <a className="btn small" href={o.whatsapp} target="_blank" rel="noreferrer">Remind on WhatsApp</a></div>)) : <p className="muted small">Nobody's overdue. <Link to="/finlens/split">Friends & splits →</Link></p>}</div>
      </div>
    </div>
  );
}

// ---------------- Wishlist ----------------
function Wishlist({ version }) {
  const [d, setD] = useState(null);
  const [f, setF] = useState({ name: "", price: "" });
  const [busy, setBusy] = useState(null);
  const load = () => fin.get("/guard/wishlist").then(setD);
  useEffect(() => { load(); }, [version]); // eslint-disable-line
  if (!d) return <Skeleton rows={2} />;
  const decide = async (id, decision) => { await fin.post(`/guard/wishlist/${id}/decide`, { decision }); load(); };
  return (
    <div className="stack">
      <div className="card hero">
        <h3 style={{ color: "#fff" }}>🧘 Think-twice wishlist</h3>
        <p style={{ margin: 0 }}>Park a want for {d.cooloff_hours} hours. Most impulses fade; the ones that don't are worth buying, often at a better price.</p>
        <div className="num" style={{ fontSize: 22, fontWeight: 800, marginTop: 8 }}>Saved by skipping: {inr(d.saved_by_skipping)}</div>
      </div>
      <form className="card row" onSubmit={async (e) => { e.preventDefault(); await fin.post("/guard/wishlist", { name: f.name, price: +f.price }); setF({ name: "", price: "" }); load(); }}>
        <input placeholder="Something you want" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
        <input type="number" min="1" placeholder="₹" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} required style={{ width: 120 }} />
        <button>Add</button>
      </form>
      {d.items.map((w) => (
        <div key={w.id} className={`card lift wish ${w.due ? "due" : ""} ${w.status}`}>
          <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
            <div><b>{w.name}</b> <span className="num">{inr(w.price)}</span> {w.status !== "waiting" && <span className={`pill ${w.status === "skipped" ? "green" : ""}`}>{w.status}</span>}
              {w.status === "waiting" && <div className="small muted">{w.due ? "⏰ Cool-off over: still want it?" : `Decide in ${w.hours_left} h`}</div>}
              {w.lenses?.length > 0 && <div className="small muted">💭 {w.lenses.join(" · ")}</div>}
              {w.best_price && <div className="small" style={{ color: "var(--emerald)" }}>Best price found: {inr(w.best_price)} {w.best_link && <a href={w.best_link} target="_blank" rel="noreferrer">view ↗</a>}</div>}</div>
            <div className="row" style={{ gap: 6 }}>
              <button className="small ghost" disabled={busy === w.id} onClick={async () => { setBusy(w.id); await fin.post(`/guard/wishlist/${w.id}/check-price`); setBusy(null); load(); }}>{busy === w.id ? "Checking…" : "Check price"}</button>
              {w.status === "waiting" && <><button className="small" onClick={() => decide(w.id, "skipped")}>🙌 Skip it</button><button className="small ghost" onClick={() => decide(w.id, "bought")}>Bought it</button></>}
              <button className="small subtle" onClick={async () => { await fin.del(`/guard/wishlist/${w.id}`); load(); }}>✕</button>
            </div>
          </div>
        </div>))}
      {!d.items.length && <p className="muted">Nothing parked. Use <b>Before you pay</b> or add something above.</p>}
    </div>
  );
}

// ---------------- Plan: emergency fund, festivals & events, buddy ----------------
function Plan({ version }) {
  const [ins, setIns] = useState(null);
  const [st, setSt] = useState(null);
  const [buddy, setBuddy] = useState(null);
  const [ev, setEv] = useState({ name: "", date: "", amount: "", yearly: false });
  const load = () => { fin.get("/guard/insights").then(setIns); fin.get("/guard/settings").then(setSt); fin.get("/guard/buddy").then(setBuddy).catch(() => {}); };
  useEffect(() => { load(); }, [version]); // eslint-disable-line
  if (!ins || !st) return <Skeleton rows={3} />;
  if (ins.empty) return <div className="notice">Add a statement or live transactions first.</div>;
  const save = async (patch) => { setSt(await fin.put("/guard/settings", patch)); setIns(await fin.get("/guard/insights")); fin.get("/guard/buddy").then(setBuddy).catch(() => {}); };
  const s = st.settings, em = ins.emergency, E = ins.events;
  return (
    <div className="stack">
      <div className="grid g2">
        <div className="card lift">
          <h3>🛟 Emergency fund</h3>
          {em.months_covered != null ? (<>
            <div className="row" style={{ gap: 14, alignItems: "center" }}>
              <Ring pct={Math.min(100, em.months_covered / Math.max(0.1, em.target_months) * 100)} color={em.status === "good" ? "green" : em.status === "low" ? "red" : "amber"} size={110}>
                <b className="num" style={{ fontSize: 20 }}>{em.months_covered}</b><span className="small muted">of {em.target_months} mo</span></Ring>
              <div className="small">Ready money {inr(em.liquid)} covers <b>{em.months_covered} months</b> of essentials ({inr(em.monthly_essentials)}/month).<br />{em.plan}</div>
            </div>
            <div className="row" style={{ gap: 8, marginTop: 10 }}>
              <label className="field" style={{ flex: 1 }}>Target months<input type="number" min="0" max="24" value={s.emergency_months} onChange={(e) => save({ emergency_months: +e.target.value })} /></label>
              <label className="field" style={{ flex: 1 }}>Savings kept elsewhere (₹)<input type="number" min="0" defaultValue={s.other_savings} onBlur={(e) => save({ other_savings: +e.target.value })} /></label>
            </div></>) : <p className="muted small">Needs your balance and a month of spending.</p>}
        </div>
        <div className="card lift">
          <h3>🤝 Accountability buddy</h3>
          <div className="row" style={{ gap: 8 }}>
            <input placeholder="Buddy's name (a friend, sibling, parent)" defaultValue={s.buddy?.name} onBlur={(e) => save({ buddy: { ...s.buddy, name: e.target.value } })} style={{ flex: 1 }} />
            <select value={s.buddy?.share} onChange={(e) => save({ buddy: { ...s.buddy, share: e.target.value } })} style={{ width: 150 }}><option value="totals">Totals only</option><option value="detailed">With categories</option></select>
          </div>
          {buddy && <pre className="buddy-preview">{buddy.text}</pre>}
          {buddy && <a className="btn small" href={buddy.whatsapp} target="_blank" rel="noreferrer">📤 Share this week on WhatsApp</a>}
          <p className="small muted">Nothing is sent automatically; you choose when and with whom.</p>
        </div>
      </div>
      <div className="card lift">
        <div className="row between"><h3 style={{ margin: 0 }}>🗓️ Festivals & big days</h3>
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={s.reserve_events} onChange={(e) => save({ reserve_events: e.target.checked })} style={{ width: 18 }} />Reserve the monthly set-aside in safe-to-spend</label></div>
        <p className="small muted">Pick what you celebrate. FinLens spreads the cost over the months before, so it never comes as a shock. Monthly set-aside now: <b>{inr(E.monthly_reserve)}</b>.</p>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {Object.entries(st.festivals).map(([k, f]) => {
            const on = s.festivals.includes(k);
            return <button key={k} className="small" onClick={() => save({ festivals: on ? s.festivals.filter((x) => x !== k) : [...s.festivals, k] })}
              style={on ? {} : { background: "#fff", color: "var(--ink)", boxShadow: `inset 0 0 0 1.5px ${E.suggested.includes(k) ? "var(--saffron)" : "var(--line)"}` }}>{f.emoji} {f.name}{E.suggested.includes(k) && !on ? " ★" : ""}</button>;
          })}
        </div>
        <p className="small muted">★ suggested for you (from your language).</p>
        <div className="stack" style={{ gap: 8, marginTop: 8 }}>
          {E.items.map((e) => (
            <div key={e.id + e.date} className="row between event-row">
              <span>{e.emoji} <b>{e.name}</b> <span className="small muted">{e.date}{e.approx ? " (approx.)" : ""} · in {e.days_left} days</span>
                {e.last_time && <div className="small" style={{ color: "var(--coral)" }}>{e.last_time}</div>}</span>
              <span className="row" style={{ gap: 6 }}>
                {e.festival ? <input type="number" min="0" defaultValue={e.amount} style={{ width: 100 }} onBlur={(x) => save({ festival_budgets: { ...s.festival_budgets, [e.id]: +x.target.value } })} title="Budget" />
                  : <b className="num">{inr(e.amount)}</b>}
                <span className="pill blue">{inr(e.monthly)}/mo</span>
                {!e.festival && <button className="small subtle" onClick={() => save({ events: s.events.filter((x) => x.id !== e.id) })}>✕</button>}
              </span>
            </div>))}
        </div>
        <form className="row" style={{ gap: 6, marginTop: 10, flexWrap: "wrap" }} onSubmit={(x) => { x.preventDefault(); save({ events: [...s.events, { id: `ev${Date.now()}`, name: ev.name, date: ev.date, amount: +ev.amount, yearly: ev.yearly, emoji: "📌" }] }); setEv({ name: "", date: "", amount: "", yearly: false }); }}>
          <input placeholder="Birthday, semester fees, trip…" value={ev.name} onChange={(x) => setEv({ ...ev, name: x.target.value })} required style={{ flex: 2 }} />
          <input type="date" value={ev.date} onChange={(x) => setEv({ ...ev, date: x.target.value })} required style={{ flex: 1 }} />
          <input type="number" min="1" placeholder="₹" value={ev.amount} onChange={(x) => setEv({ ...ev, amount: x.target.value })} required style={{ width: 110 }} />
          <label className="row small" style={{ gap: 4 }}><input type="checkbox" checked={ev.yearly} onChange={(x) => setEv({ ...ev, yearly: x.target.checked })} style={{ width: 16 }} />every year</label>
          <button>Add</button>
        </form>
      </div>
    </div>
  );
}

// ---------------- Make it mine ----------------
function Chips({ options, value, onChange, multi }) {
  const on = (k) => (multi ? value.includes(k) : value === k);
  return (
    <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
      {options.map(([k, label]) => (
        <button type="button" key={k} className="small" onClick={() => onChange(multi ? (on(k) ? value.filter((x) => x !== k) : [...value, k]) : k)}
          style={on(k) ? {} : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }}>{label}</button>))}
    </div>
  );
}

function Mine() {
  const g = useGuard();
  const [st, setSt] = useState(null);
  const [cap, setCap] = useState({ key: "Shopping", amount: "" });
  const [saved, setSaved] = useState(false);
  useEffect(() => { fin.get("/guard/settings").then(setSt); }, []);
  if (!st) return <Skeleton rows={4} />;
  const s = st.settings;
  const save = async (patch) => { const r = await fin.put("/guard/settings", patch); setSt(r); g.setSettings(r.settings); setSaved(true); setTimeout(() => setSaved(false), 1200); };
  const rule = async (scope, key, nature) => { await fin.post("/guard/rules", { scope, key, nature }); setSt(await fin.get("/guard/settings")); };
  const num = (k, label, extra = {}) => (
    <label className="field">{label}<input type="number" defaultValue={s[k]} key={`${k}-${s[k]}`} onBlur={(e) => +e.target.value !== s[k] && save({ [k]: +e.target.value })} {...extra} /></label>);
  const TONE_SAMPLE = { gentle: "Small check-in: ₹1,499 at Lenskart. Totally fine if it was planned. 💛", straight: "₹1,499 at Lenskart is a want. It cost 15 days of your Goa saving.", tough: "₹1,499 on Lenskart? Was it really worth it? 🤨" };
  return (
    <div className="stack">
      {saved && <div className="saved-pill">✓ saved</div>}
      {st.why_defaults.length > 0 && <div className="card" style={{ background: "var(--sky-bg)" }}><b>Why FinLens started like this for you</b><ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>{st.why_defaults.map((w) => <li key={w}>{w}</li>)}</ul></div>}
      <div className="card lift">
        <h3>🗣️ How should FinLens talk to you?</h3>
        <div className="grid g3">{["gentle", "straight", "tough"].map((t) => (
          <button type="button" key={t} className={`tone-card ${s.tone === t ? "on" : ""}`} onClick={() => save({ tone: t })}>
            <b>{{ gentle: "💛 Gentle friend", straight: "📏 Straight talk", tough: "🔥 Tough love" }[t]}</b><span className="small">{TONE_SAMPLE[t]}</span></button>))}</div>
        <div className="grid g3" style={{ marginTop: 12 }}>
          <label className="field">Sensitivity<Chips options={[["relaxed", "Relaxed"], ["balanced", "Balanced"], ["strict", "Strict"]]} value={s.sensitivity} onChange={(v) => save({ sensitivity: v })} /></label>
          {num("min_alert", "Don't alert for wants under ₹", { min: 0 })}
          <label className="field">Quiet hours (no pop-ups; fraud alerts still come)<div className="row" style={{ gap: 6 }}>
            <input type="time" defaultValue={s.quiet_start} onBlur={(e) => save({ quiet_start: e.target.value })} /><span>to</span><input type="time" defaultValue={s.quiet_end} onBlur={(e) => save({ quiet_end: e.target.value })} /></div></label>
        </div>
      </div>
      <div className="card lift">
        <h3>💭 Show me what things cost in…</h3>
        <Chips multi options={[["goal", "🎯 days of goal saving"], ["work", "⏱️ hours of my work"], ["pocket", "📅 days of my monthly money"], ["room", "👛 % of safe-to-spend"], ["daily", "🚦 % of today's limit"], ["unit", "🍛 my own unit"]]}
          value={s.cost_lens} onChange={(v) => save({ cost_lens: v })} />
        <div className="grid g3" style={{ marginTop: 10 }}>
          <label className="field">My unit (name)<input defaultValue={s.my_unit?.name} onBlur={(e) => save({ my_unit: { ...s.my_unit, name: e.target.value } })} /></label>
          <label className="field">One unit costs ₹<input type="number" min="1" defaultValue={s.my_unit?.price} onBlur={(e) => save({ my_unit: { ...s.my_unit, price: +e.target.value } })} /></label>
          {num("work_hours_month", "My working hours a month", { min: 20 })}
        </div>
      </div>
      <div className="card lift">
        <h3>⚖️ What's a need for <i>me</i>?</h3>
        <p className="small muted">Tap to change. Your choice beats FinLens's guess everywhere: alerts, daily limit, insights.</p>
        <div className="nature-grid">
          {Object.entries(st.grid).map(([grp, subs]) => (
            <div key={grp}><div className="small muted" style={{ fontWeight: 800, margin: "8px 0 4px" }}>{grp}</div>
              {subs.map((x) => {
                const cur = x.mine || x.default;
                return (
                  <div key={x.key} className="row between nature-row"><span className="small">{x.key}{x.mine && <span className="pill blue" style={{ marginLeft: 4 }}>yours</span>}</span>
                    <span className="seg">{["need", "flex", "want"].map((n) => <button type="button" key={n} className={cur === n ? `on ${n}` : ""} onClick={() => rule("subcategory", x.key, n === x.default ? "default" : n)}>{NAT[n][0]}</button>)}</span></div>);
              })}</div>))}
        </div>
        <div className="grid g3" style={{ marginTop: 12 }}>
          {num("delivery_per_week", "Food deliveries a week that are fine", { min: 0 })}
          {num("meal_comfort", "My normal meal costs ₹ (0 = learn from history)", { min: 0 })}
          {num("clothing_need_max", "Everyday clothing up to ₹", { min: 0 })}
          {num("flex_check_above", "Ask me about 'depends' spends above ₹", { min: 0 })}
        </div>
        {st.merchant_rules.length > 0 && <><div className="small muted" style={{ fontWeight: 800, marginTop: 10 }}>Your merchant rules</div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{st.merchant_rules.map((r) => <span key={r.id} className={`pill ${NAT[r.nature]?.[1]}`}>{r.key}: {NAT[r.nature]?.[0]} <button type="button" className="pill-x" onClick={() => rule("merchant", r.key, "default")}>✕</button></span>)}</div></>}
      </div>
      <div className="grid g2">
        <div className="card lift">
          <h3>🚦 Daily limit</h3>
          <Chips options={[["auto", "Auto (from safe-to-spend)"], ["fixed", "My own number"]]} value={s.daily_limit_mode} onChange={(v) => save({ daily_limit_mode: v })} />
          <div className="grid g2" style={{ marginTop: 10 }}>
            {s.daily_limit_mode === "fixed" && num("daily_limit", "₹ per day", { min: 0 })}
            <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={s.rollover} onChange={(e) => save({ rollover: e.target.checked })} style={{ width: 18 }} />Unspent money rolls over</label>
          </div>
          <h3 style={{ marginTop: 14 }}>🎚️ Monthly caps</h3>
          {Object.entries(s.caps).map(([k, v]) => <div key={k} className="row between small" style={{ marginBottom: 4 }}><span>{k}</span><span><b className="num">{inr(v)}</b> <button type="button" className="small subtle" onClick={() => { const c = { ...s.caps }; delete c[k]; save({ caps: c }); }}>✕</button></span></div>)}
          <div className="row" style={{ gap: 6 }}>
            <select value={cap.key} onChange={(e) => setCap({ ...cap, key: e.target.value })}>{["Food", "Shopping", "Entertainment", "Transportation", "Food delivery", "Cafe", "Clothing", "Gadgets", "Subscription", "Betting & fantasy", "Alcohol & smoking"].map((c) => <option key={c}>{c}</option>)}</select>
            <input type="number" min="1" placeholder="₹ a month" value={cap.amount} onChange={(e) => setCap({ ...cap, amount: e.target.value })} style={{ width: 120 }} />
            <button type="button" className="small" disabled={!cap.amount} onClick={() => { save({ caps: { ...s.caps, [cap.key]: +cap.amount } }); setCap({ ...cap, amount: "" }); }}>Add cap</button>
          </div>
          <p className="small muted">Alerts at 50%, 80% and 100% of each cap.</p>
        </div>
        <div className="card lift">
          <h3>🫙 Jar, cool-off & safety</h3>
          <div className="grid g2">
            {num("want_tax_pct", "Want tax: % of each want to save", { min: 0, max: 100 })}
            <label className="field">Round every payment up to<select value={s.roundup_to} onChange={(e) => save({ roundup_to: +e.target.value })}>{[0, 10, 50, 100].map((x) => <option key={x} value={x}>{x ? `₹${x}` : "off"}</option>)}</select></label>
            <label className="field">Jar goes to<select value={s.jar_goal_id || ""} onChange={(e) => save({ jar_goal_id: e.target.value || null })}><option value="">My most urgent goal</option>{st.goals.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
            {num("cooloff_amount", "Suggest a cool-off for wants above ₹", { min: 0 })}
            {num("cooloff_hours", "Cool-off length (hours)", { min: 1 })}
            {num("big_new_merchant", "'Was this you?' for new merchants above ₹", { min: 100 })}
          </div>
          <label className="row small" style={{ gap: 6, marginTop: 6 }}><input type="checkbox" checked={s.fraud_alerts} onChange={(e) => save({ fraud_alerts: e.target.checked })} style={{ width: 18 }} />Fraud & double-charge alerts</label>
          <h3 style={{ marginTop: 14 }}>🌙 Night summary</h3>
          <div className="row" style={{ gap: 10 }}>
            <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={s.voice_summary} onChange={(e) => save({ voice_summary: e.target.checked })} style={{ width: 18 }} />Remind me at</label>
            <input type="time" defaultValue={s.summary_time} onBlur={(e) => save({ summary_time: e.target.value })} style={{ width: 130 }} />
            <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={s.notify_browser} onChange={(e) => save({ notify_browser: e.target.checked })} style={{ width: 18 }} />Browser notifications</label>
          </div>
          <p className="small muted">The summary speaks in your FinLens language (English, हिन्दी or ಕನ್ನಡ).</p>
        </div>
      </div>
    </div>
  );
}

export default function SpendGuard() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "today";
  const g = useGuard();
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Spend Guard 🚦</h1>
        <p className="muted">Watches every rupee as it leaves, quietly for needs and loudly for wants, fees and anything suspicious. Everything is tuned to you.</p></div></div>
      <div className="guard-tabs">{TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setSp({ tab: k })}>{l}{k === "alerts" && g.unread > 0 && <span className="pill red" style={{ marginLeft: 6 }}>{g.unread}</span>}</button>)}</div>
      {tab === "today" && <Today version={g.version} />}
      {tab === "live" && <Live />}
      {tab === "alerts" && <Alerts version={g.version} />}
      {tab === "insights" && <Insights version={g.version} />}
      {tab === "wishlist" && <Wishlist version={g.version} />}
      {tab === "plan" && <Plan version={g.version} />}
      {tab === "mine" && <Mine />}
    </div>
  );
}
