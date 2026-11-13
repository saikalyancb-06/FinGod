import { useEffect, useRef, useState } from "react";
import { fin, inr } from "../api.js";
import { Typing } from "../ui.jsx";
import { useLang, useT } from "../i18n.jsx";
import { MicButton, SpeakButton } from "../voice.jsx";

const START = [["🍛", "I want to order biryani"], ["👟", "I need shoes for college"], ["🎧", "I want to buy headphones"], ["🍕", "Pizza for dinner"]];
const THUMB = { food: "🍽️", fashion: "👕", shopping: "📦" };
const BAR = [["budget_fit", "#00a676", 40], ["rating_quality", "#ffb020", 30], ["preference_match", "#1e9bf0", 20], ["availability", "#ff6b4a", 10]];

function WishBtn({ r }) {
  const [done, setDone] = useState(false);
  return <button className="small subtle" style={{ marginTop: 6, display: "block", marginLeft: "auto" }} disabled={done}
    onClick={async () => { await fin.post("/guard/wishlist", { name: r.title.slice(0, 150), price: r.price, link: r.link }); setDone(true); }}>{done ? "✓ in wishlist" : "♡ Wishlist"}</button>;
}

function Results({ res, category }) {
  return (
    <div className="card" style={{ marginTop: 6, padding: 10 }}>
      {res.notice && <div className="notice" style={{ marginBottom: 8 }}>{res.notice}</div>}
      {res.results.map((r, i) => (
        <div key={r.title + i} className="rec" style={{ animationDelay: `${i * 0.07}s` }}>
          {r.thumbnail ? <img src={r.thumbnail} alt="" /> : <div className="thumb" style={{ background: ["#ffece6", "#e3f8ef", "#fff4dc", "#e4f3fe", "#fde8ee"][i % 5] }}>{THUMB[category] || "⭐"}</div>}
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 6 }}><span className="pill" style={{ background: "var(--ink)", color: "#fff" }}>#{i + 1}</span><b>{r.title}</b>{r.is_demo && <span className="pill amber">DEMO</span>}</div>
            <div className="small muted">
              {r.rating ? <span className="stars">★ {r.rating}</span> : "no rating"}{r.reviews ? ` (${Number(r.reviews).toLocaleString("en-IN")})` : ""} · {r.subtitle || r.source}
              {r.address ? ` · ${r.address}` : ""}
            </div>
            <div className="small" style={{ marginTop: 3 }}>{r.why}</div>
            <div className="score-bar" title={`Score ${r.score}/100`}>{BAR.map(([k, c]) => <i key={k} style={{ width: `${r.score_breakdown[k]}%`, background: c }} />)}</div>
            <div className="small muted" style={{ marginTop: 3 }}>
              {r.savings?.per_order > 0 && <span className="pill green">saves ~{inr(r.savings.per_order)}/order (est.)</span>}{" "}
              {r.goal_impact?.days >= 0.5 && <span className={`pill ${r.goal_impact.days > 3 ? "amber" : "blue"}`} title="Days of goal saving this purchase uses">🎯 {r.goal_impact.text}</span>}{" "}
              Source: {r.source} · retrieved {new Date(r.retrieved_at).toLocaleString("en-IN")}
            </div>
          </div>
          <div className="price-col" style={{ textAlign: "right" }}>
            <div className="price" style={{ color: r.within_budget ? "var(--emerald)" : "var(--ink)" }}>{r.price != null ? inr(r.price) : "—"}</div>
            <div className="small muted" style={{ maxWidth: 140 }}>{r.price != null ? r.price_basis : "price not listed"}</div>
            {r.link && <a className="btn small ghost" style={{ marginTop: 6 }} href={r.link} target="_blank" rel="noreferrer">View ↗</a>}
            {r.price != null && category !== "food" && <WishBtn r={r} />}
          </div>
        </div>
      ))}
      <div className="legend" style={{ padding: "8px 14px" }}>{BAR.map(([k, c, w]) => <span key={k}><i style={{ background: c }} />{k.replace("_", " ")} ({w})</span>)}</div>
    </div>
  );
}

export default function SmartSpending({ me }) {
  const tr = useT();
  const lang = useLang();
  const [msgs, setMsgs] = useState([]);
  const [sid, setSid] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState(null);
  const [consent, setConsent] = useState(me.consents.smart_spending);
  const end = useRef();
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);
  const [room, setRoom] = useState(null);
  const loadRoom = () => fin.get("/spending/room").then(setRoom).catch(() => {});
  useEffect(() => { loadRoom(); }, []);
  const send = async (t) => { // eslint-disable-line
    if (!t.trim()) return;
    setMsgs((m) => [...m, { role: "user", text: t }]);
    setText(""); setBusy(true);
    try {
      const r = await fin.post("/spending/message", { text: t, session_id: sid, language: lang });
      setSid(r.session_id);
      setMsgs((m) => [...m, { role: "assistant", text: r.reply, results: r.results, category: r.state?.category, quick: r.quick_replies, confirm: r.confirm }]);
      if (r.done) { setLast(r.results); setSid(null); loadRoom(); }
    } catch (e) { setMsgs((m) => [...m, { role: "assistant", text: `Sorry, ${e.message}` }]); } finally { setBusy(false); }
  };
  const reset = async () => { await fin.post("/spending/reset"); setSid(null); setLast(null); setMsgs([]); };
  const ctx = last?.spending_context;
  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 320px", alignItems: "start" }}>
      <div className="chat-page" style={{ minWidth: 0 }}>
        <div className="page-head reveal" style={{ marginBottom: 12 }}><div><h1>{tr("spend_title")} 🛍️</h1><p className="muted">{tr("spend_sub")}</p></div>
          <button className="subtle small" onClick={reset}>{tr("new_search")}</button></div>
        <div className="suggestions reveal" style={{ marginBottom: 12 }}>{tr("spend_start").map(([e, s]) => <button key={s} className="small" disabled={busy} onClick={() => send(s)}>{e} {s}</button>)}</div>
        <div className="card chat-card">
          <div className="chat-scroll">
            <div className="chat">
              {[{ role: "assistant", text: tr("spend_hello") }, ...msgs].map((m, i) => (
                <div key={i}>
                  <div className={`msg ${m.role === "user" ? "user" : ""}`}>
                    {m.role !== "user" && <div className="avatar">✦</div>}
                    <div className="bubble" style={m.confirm ? { boxShadow: "inset 0 0 0 2px var(--amber, #ffb020)", background: "#fff8e6" } : undefined}>{m.confirm && <b>⚠️ </b>}{m.text}{m.role !== "user" && i > 0 && <div style={{ marginTop: 6 }}><SpeakButton text={m.text} /></div>}</div>
                    {m.role === "user" && <div className="avatar me">🙂</div>}
                  </div>
                  {m.quick?.length > 0 && i === msgs.length && !busy && (
                    <div className="row" style={{ gap: 6, margin: "6px 0 4px 52px", flexWrap: "wrap" }}>
                      {m.quick.map((q, k) => <button key={q} className={`small ${k ? "ghost" : ""}`} onClick={() => send(q)}>{k ? `Use ${q}` : `✓ ${q}`}</button>)}
                    </div>)}
                  {m.results?.results?.length > 0 && <Results res={m.results} category={m.category} />}
                </div>
              ))}
              {busy && <Typing />}
              <div ref={end} />
            </div>
          </div>
          <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <MicButton disabled={busy} onText={(x) => send(x)} />
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder={tr("spend_ph")} />
            <button disabled={busy}>{tr("send")}</button>
          </form>
        </div>
      </div>
      <div className="stack side-sticky">
        {room && room.safe_to_spend != null && (
          <div className="card hero reveal">
            <h3 style={{ color: "#fff" }}>👛 {tr("room")}</h3>
            <div className="num" style={{ fontSize: 30, fontWeight: 700 }}>{inr(room.safe_to_spend)}</div>
            <div className="small" style={{ opacity: .9 }}>safe to spend {room.period} · about <b>{inr(room.daily_room)}/day</b></div>
            <div className="small" style={{ opacity: .85, marginTop: 6 }}>{room.source === "statement" ? "From your statement (after commitments, goal savings and buffer)." : "From your profile (income − fixed costs − goal savings − buffer)."}</div>
            {room.suggested?.food?.cap && <div className="small" style={{ marginTop: 8, position: "relative", zIndex: 1 }}>🍛 Comfortable food order: <b>≈ {inr(room.suggested.food.cap)}</b> per person</div>}
            {room.primary_goal && (
              <div style={{ marginTop: 10, background: "rgba(255,255,255,.16)", borderRadius: 12, padding: "8px 10px", position: "relative", zIndex: 1 }}>
                <div className="small">🎯 <b>{room.primary_goal.name}</b>: {({ on_track: "on track", ahead: "ahead of plan", behind: "behind plan", overdue: "past its date", no_deadline: "no deadline" })[room.primary_goal.status] || room.primary_goal.status}</div>
                {room.primary_goal.required_monthly != null && <div className="small" style={{ opacity: .9 }}>needs {inr(room.primary_goal.required_monthly)}/month{room.primary_goal.days_left != null ? (room.primary_goal.days_left >= 0 ? ` · ${room.primary_goal.days_left} days left` : " · past its deadline") : ""}</div>}
              </div>)}
          </div>
        )}
        {room?.goal_warning && <div className="notice reveal small">🎯 {room.goal_warning.text} <a href="/finlens/planner">Adjust in Planner →</a></div>}
        {room && room.safe_to_spend == null && (
          <div className="notice reveal small">Complete your profile (Settings) or upload a statement so FinLens can tell you what a comfortable budget is.</div>
        )}
        <div className="card hero warm reveal">
          <h3 style={{ color: "#fff" }}>💡 Your savings lens</h3>
          {ctx?.typical_spend ? (
            <p style={{ margin: 0 }}>You typically spend about <b>{inr(ctx.typical_spend)}</b> per comparable purchase, roughly <b>{ctx.monthly_frequency}×/month</b> ({ctx.based_on}).
              {last.savings_headline && <><br /><br /><b>{last.savings_headline}</b></>}</p>
          ) : consent ? <p style={{ margin: 0 }}>Run a search to compare options with what you usually spend.</p>
            : <p style={{ margin: 0 }}>Turn on personalisation to estimate savings against your typical spend. Only a small summary is used, never your full statement.</p>}
          {!consent && <button className="small" style={{ marginTop: 12, background: "#fff", color: "var(--coral)", position: "relative", zIndex: 1 }}
            onClick={async () => { await fin.post("/consents", { purpose: "smart_spending", granted: true }); setConsent(true); }}>Enable personalisation</button>}
        </div>
        <div className="card reveal">
          <h3>🧭 How ranking works</h3>
          <p className="small muted" style={{ margin: 0 }}>Budget fit (40) + rating quality, smoothed by review count (30) + match to your preferences (20) + availability or delivery (10). Results are real listings from the source shown on each card.</p>
        </div>
        <div className="notice reveal small">🛡️ {last?.purchase_control || "FinLens only recommends. It never places orders or buys anything for you."} Savings are estimates, not guarantees.</div>
      </div>
    </div>
  );
}
