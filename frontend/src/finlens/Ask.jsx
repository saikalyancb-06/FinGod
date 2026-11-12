import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fin } from "../api.js";
import { Md, Typing } from "../ui.jsx";

const SUGGESTED = [
  "Where is most of my money going?", "Why am I running out of money?", "How much did I spend on food delivery?",
  "Can I afford headphones for ₹2,000?", "What subscriptions do I have?", "What will my balance be at month end?",
];

export default function Ask() {
  const [sp] = useSearchParams();
  const [msgs, setMsgs] = useState([{ role: "assistant", text: "Hi! Ask me anything about your spending. I answer only from figures FinLens has calculated from your statement. 💬" }]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [showFacts, setShowFacts] = useState(null);
  const end = useRef();
  const asked = useRef(false);
  const ask = async (q) => {
    if (!q.trim()) return;
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setText(""); setBusy(true);
    try {
      const r = await fin.post("/ask", { question: q });
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, facts: r.facts, llm: r.llm_used, intent: r.intent, check: r.number_check }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: `Sorry, ${e.message}` }]);
    } finally { setBusy(false); }
  };
  useEffect(() => { const q = sp.get("q"); if (q && !asked.current) { asked.current = true; ask(q); } }, []); // eslint-disable-line
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);
  return (
    <div className="chat-page" style={{ maxWidth: 940 }}>
      <div className="page-head reveal" style={{ marginBottom: 12 }}><div><h1>Ask FinLens ✨</h1><p className="muted">Answers are grounded in backend-calculated numbers. Open “facts used” on any reply to check them.</p></div></div>
      <div className="suggestions reveal" style={{ marginBottom: 12 }}>{SUGGESTED.map((s) => <button key={s} className="small" onClick={() => ask(s)} disabled={busy}>{s}</button>)}</div>
      <div className="card chat-card">
        <div className="chat-scroll">
        <div className="chat">
          {msgs.map((m, i) => (
            <div key={i} className={`msg ${m.role === "user" ? "user" : ""}`}>
              {m.role !== "user" && <div className="avatar">✦</div>}
              <div className="bubble" style={{ whiteSpace: m.role === "user" ? "pre-wrap" : "normal" }}>
                {m.role === "user" ? m.text : <Md text={m.text} />}
                {m.facts && (
                  <div style={{ marginTop: 8 }}>
                    <span className="pill green">{m.llm ? "explained by AI" : "template answer"}</span>{" "}
                    {m.llm && <span className="pill blue" title="Every ₹ figure was matched against backend facts">✓ figures verified</span>}
                    {m.check?.fell_back && <span className="pill amber" title={`AI used unverified figures: ${m.check.unverified_numbers.join(", ")}`}>AI answer replaced: unverified figures</span>} <span className="pill">{m.intent}</span>{" "}
                    <button className="small ghost" onClick={() => setShowFacts(showFacts === i ? null : i)}>{showFacts === i ? "hide" : "facts used"}</button>
                    {showFacts === i && <pre className="small" style={{ whiteSpace: "pre-wrap", background: "#fffaf2", padding: 10, borderRadius: 10, maxHeight: 260, overflow: "auto" }}>{JSON.stringify(m.facts, null, 2)}</pre>}
                  </div>
                )}
              </div>
              {m.role === "user" && <div className="avatar me">🙂</div>}
            </div>
          ))}
          {busy && <Typing />}
          <div ref={end} />
        </div>
        </div>
        <form className="chat-input" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Why did my food spending go up?" />
          <button disabled={busy}>Ask</button>
        </form>
      </div>
    </div>
  );
}
