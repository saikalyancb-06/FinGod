import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fin } from "../api.js";
import { useLang, useT } from "../i18n.jsx";
import { Md, Typing } from "../ui.jsx";
import { MicButton, SpeakButton } from "../voice.jsx";

export default function Ask() {
  const t = useT();
  const lang = useLang();
  const [sp] = useSearchParams();
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [showFacts, setShowFacts] = useState(null);
  const [health, setHealth] = useState(null);
  useEffect(() => { fin.get("/money-health").then(setHealth).catch(() => {}); }, []);
  // never suggest "why am I running out of money?" to someone who isn't
  const chips = t("ask_suggest").map((s, i) => (i === 1 && health && !health.is_running_out ? t("ask_health") : s));
  const end = useRef();
  const asked = useRef(false);
  const ask = async (q) => {
    if (!q.trim()) return;
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setText(""); setBusy(true);
    try {
      const r = await fin.post("/ask", { question: q, language: lang });
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, en: r.answer_en, qen: r.question_en, facts: r.facts, llm: r.llm_used, intent: r.intent, check: r.number_check }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: `Sorry, ${e.message}` }]);
    } finally { setBusy(false); }
  };
  useEffect(() => { const q = sp.get("q"); if (q && !asked.current) { asked.current = true; ask(q); } }, []); // eslint-disable-line
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);
  const all = [{ role: "assistant", text: t("ask_hello") }, ...msgs];
  return (
    <div className="chat-page" style={{ maxWidth: 940 }}>
      <div className="page-head reveal" style={{ marginBottom: 12 }}><div><h1>{t("ask_title")} ✨</h1><p className="muted">{t("ask_sub")}</p></div>
        {lang !== "en" && <span className="pill blue">🗣️ Sarvam AI · {lang === "hi" ? "हिन्दी" : "ಕನ್ನಡ"}</span>}</div>
      <div className="suggestions reveal" style={{ marginBottom: 12 }}>{chips.map((s) => <button key={s} className="small" onClick={() => ask(s)} disabled={busy}>{s}</button>)}</div>
      <div className="card chat-card">
        <div className="chat-scroll">
          <div className="chat">
            {all.map((m, i) => (
              <div key={i} className={`msg ${m.role === "user" ? "user" : ""}`}>
                {m.role !== "user" && <div className="avatar">✦</div>}
                <div className="bubble" style={{ whiteSpace: m.role === "user" ? "pre-wrap" : "normal" }}>
                  {m.role === "user" ? m.text : <Md text={m.text} />}
                  {m.facts && (
                    <div style={{ marginTop: 8 }}>
                      <span className="pill green">{m.llm ? "explained by AI" : "template answer"}</span>{" "}
                      {m.llm && <span className="pill blue" title="Every ₹ figure was matched against backend facts">✓ figures verified</span>}{" "}
                      {m.check?.fell_back && <span className="pill amber" title={`AI used unverified figures: ${m.check.unverified_numbers.join(", ")}`}>AI answer replaced: unverified figures</span>}{" "}
                      <SpeakButton text={m.text} />
                      <button className="small ghost" onClick={() => setShowFacts(showFacts === i ? null : i)}>{showFacts === i ? "hide" : "facts used"}</button>
                      {showFacts === i && (
                        <div>
                          {m.qen && <div className="small muted">Understood as: “{m.qen}”</div>}
                          {m.en && <details className="small"><summary>English original</summary><Md text={m.en} /></details>}
                          <pre className="small" style={{ whiteSpace: "pre-wrap", background: "#fffaf2", padding: 10, borderRadius: 10, maxHeight: 260, overflow: "auto" }}>{JSON.stringify(m.facts, null, 2)}</pre>
                        </div>)}
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
          <MicButton disabled={busy} onText={(x) => ask(x)} />
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder={t("ask_ph")} />
          <button disabled={busy}>{t("ask_btn")}</button>
        </form>
      </div>
    </div>
  );
}
