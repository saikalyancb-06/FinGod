import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fin } from "../api.js";

export function UploadCard({ onUploaded }) {
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const [report, setReport] = useState(null);
  const input = useRef();
  const upload = async (blob, name) => {
    setErr(""); setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", blob, name);
      const r = await fin.post("/statements", fd);
      setReport(r);
      onUploaded?.(r);
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  const useSample = async () => {
    const r = await fetch("/x-fin/api/sample-statement");
    upload(await r.blob(), "finlens_sample_statement_SYNTHETIC.csv");
  };
  return (
    <div className="card stack reveal">
      <h3><span className="chip-ico" style={{ background: "var(--saffron-bg)" }}>📄</span>Upload a bank statement (CSV or PDF)</h3>
      <div className={`dropzone ${over ? "over" : ""}`} onClick={() => input.current.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) upload(f, f.name); }}>
        <div className="big">{busy ? "⏳" : "⬆️"}</div>
        <b>{busy ? "Cleaning and categorising…" : "Drop your CSV or PDF here, or click to choose"}</b>
        <div className="muted small">CSV: Date, Description/Narration, Type + Amount (or Debit/Credit), optional Balance. PDF: bank statements with dated lines or tables, and UPI-app statements. Stored encrypted.</div>
        <input ref={input} type="file" accept=".csv,.pdf" hidden onChange={(e) => e.target.files[0] && upload(e.target.files[0], e.target.files[0].name)} />
      </div>
      <div className="row"><button className="warm" disabled={busy} onClick={useSample}>✨ Try the synthetic sample statement</button></div>
      {err && <div className="error">{err}</div>}
      {report && (
        <div className="notice info">
          ✅ Processed {report.rows_in} rows{report.parsed_from?.startsWith("pdf") ? ` (from PDF, ${report.parsed_from.slice(4)} parser)` : ""} → {report.rows_out} transactions ({report.transactions_added} new).
          {report.warnings?.map((w) => <div key={w}>• {w}</div>)}
          {report.errors?.slice(0, 5).map((w) => <div key={w}>• {w}</div>)}
        </div>
      )}
    </div>
  );
}

const LABELS = { analytics: ["📊", "Spending analytics"], smart_spending: ["🛍️", "Smart Spending personalisation"], credit: ["🏦", "Credit affordability"] };

export default function Onboarding({ me, onReady }) {
  const nav = useNavigate();
  const [consents, setConsents] = useState(me.consents);
  const toggle = async (purpose, granted) => setConsents(await fin.post("/consents", { purpose, granted }));
  return (
    <div className="stack" style={{ maxWidth: 780 }}>
      <div className="card hero reveal">
        <h1 style={{ color: "#fff" }}>Hi {me.name.split(" ")[0]}! 👋</h1>
        <p style={{ margin: 0, fontSize: 15 }}>Two quick steps and FinLens will show you where your money goes and how to make it go further.</p>
      </div>
      <div className="card stack reveal">
        <h3><span className="chip-ico" style={{ background: "var(--mint-bg)" }}>1</span>Choose what FinLens may do with your data</h3>
        {Object.entries(me.purposes).map(([p, text]) => (
          <label key={p} className="check">
            <input type="checkbox" checked={!!consents[p]} onChange={(e) => toggle(p, e.target.checked)} />
            <span><b>{LABELS[p][0]} {LABELS[p][1]}</b>
              {p === "analytics" && <span className="pill green" style={{ marginLeft: 8 }}>required to start</span>}<br />
              <span className="muted small">{text}</span></span>
          </label>
        ))}
        <p className="muted small">Each purpose is separate and can be revoked anytime in Settings.</p>
      </div>
      {consents.analytics ? (
        <UploadCard onUploaded={async (r) => { if (r.transactions_added || r.rows_out) { await onReady(); nav("/finlens"); } }} />
      ) : (
        <div className="notice">Turn on “Spending analytics” to upload a statement.</div>
      )}
    </div>
  );
}
