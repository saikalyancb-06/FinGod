import { useEffect, useRef, useState } from "react";
import { fin, inr } from "../api.js";
import { Typing } from "../ui.jsx";

const START = [["🍛", "I want to order biryani"], ["👟", "I need shoes for college"], ["🎧", "I want to buy headphones"], ["🍕", "Pizza for dinner"]];
const THUMB = { food: "🍽️", fashion: "👕", shopping: "📦" };
const BAR = [["budget_fit", "#00a676", 40], ["rating_quality", "#ffb020", 30], ["preference_match", "#1e9bf0", 20], ["availability", "#ff6b4a", 10]];

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
              Source: {r.source} · retrieved {new Date(r.retrieved_at).toLocaleString("en-IN")}
            </div>
          </div>
          <div className="price-col" style={{ textAlign: "right" }}>
            <div className="price" style={{ color: r.within_budget ? "var(--emerald)" : "var(--ink)" }}>{r.price != null ? inr(r.price) : "—"}</div>
            <div className="small muted" style={{ maxWidth: 140 }}>{r.price != null ? r.price_basis : "price not listed"}</div>
            {r.link && <a className="btn small ghost" style={{ marginTop: 6 }} href={r.link} target="_blank" rel="noreferrer">View ↗</a>}
          </div>
        </div>
      ))}
      <div className="legend" style={{ padding: "8px 14px" }}>{BAR.map(([k, c, w]) => <span key={k}><i style={{ background: c }} />{k.replace("_", " ")} ({w})</span>)}</div>
    </div>
  );
}

export default function SmartSpending({ me }) {
  const [msgs, setMsgs] = useState([{ role: "assistant", text: "What are you looking for today: food, fashion, or something else to buy? 🛍️" }]);
  const [sid, setSid] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState(null);
  const [consent, setConsent] = useState(me.consents.smart_spending);
  const end = useRef();
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);
  const send = async (t) => {
    if (!t.trim()) return;
    setMsgs((m) => [...m, { role: "user", text: t }]);
    setText(""); setBusy(true);
    try {
      const r = await fin.post("/spending/message", { text: t, session_id: sid });
      setSid(r.session_id);
      setMsgs((m) => [...m, { role: "assistant", text: r.reply, results: r.results, category: r.state?.category }]);
      if (r.done) { setLast(r.results); setSid(null); }
    } catch (e) { setMsgs((m) => [...m, { role: "assistant", text: `Sorry, ${e.message}` }]); } finally { setBusy(false); }
  };
  const reset = async () => { await fin.post("/spending/reset"); setSid(null); setLast(null); setMsgs([{ role: "assistant", text: "Fresh start! Food, fashion or something else? 🛍️" }]); };
  const ctx = last?.spending_context;
  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 320px", alignItems: "start" }}>
      <div className="chat-page" style={{ minWidth: 0 }}>
        <div className="page-head reveal" style={{ marginBottom: 12 }}><div><h1>Smart Spending 🛍️</h1><p className="muted">Spend better, not just less. Tell me what you want and your budget, and I'll find five real options.</p></div>
          <button className="subtle small" onClick={reset}>↺ New search</button></div>
        <div className="suggestions reveal" style={{ marginBottom: 12 }}>{START.map(([e, s]) => <button key={s} className="small" disabled={busy} onClick={() => send(s)}>{e} {s}</button>)}</div>
        <div className="card chat-card">
          <div className="chat-scroll">
            <div className="chat">
              {msgs.map((m, i) => (
                <div key={i}>
                  <div className={`msg ${m.role === "user" ? "user" : ""}`}>
                    {m.role !== "user" && <div className="avatar">✦</div>}
                    <div className="bubble">{m.text}</div>
                    {m.role === "user" && <div className="avatar me">🙂</div>}
                  </div>
                  {m.results?.results?.length > 0 && <Results res={m.results} category={m.category} />}
                </div>
              ))}
              {busy && <Typing />}
              <div ref={end} />
            </div>
          </div>
          <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Chicken biryani under ₹250, delivery" />
            <button disabled={busy}>Send</button>
          </form>
        </div>
      </div>
      <div className="stack side-sticky">
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
