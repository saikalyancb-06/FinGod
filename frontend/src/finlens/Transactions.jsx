import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fin, inr } from "../api.js";
import { CAT_COLORS, CAT_EMOJI } from "../ui.jsx";
import { UploadCard } from "./Onboarding.jsx";
import { NaturePill } from "./SpendGuard.jsx";

const NEXT = { need: "want", want: "flex", flex: "need" };

export default function Transactions({ onChange }) {
  const [sp, setSp] = useSearchParams();
  const [rows, setRows] = useState(null);
  const [cats, setCats] = useState({ spending: [], credit: [] });
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState("");
  const [nat, setNat] = useState({});
  const loadNat = () => fin.get("/guard/natures").then(setNat).catch(() => {});
  useEffect(() => { loadNat(); }, []);
  const flip = async (t) => {
    const cur = nat[t.id]?.nature || "flex";
    const n = NEXT[cur] || "need";
    await fin.post("/guard/rules", { scope: "merchant", key: t.merchant_key, nature: n });
    setMsg(`✅ ${t.merchant || t.description} is now always a ${n === "flex" ? "'depends'" : n} for you. Spend Guard uses this for alerts and limits.`);
    loadNat();
  };
  const category = sp.get("category") || "";
  const month = sp.get("month") || "";
  const load = () => {
    const p = new URLSearchParams();
    if (category) p.set("category", category);
    if (month) p.set("month", month);
    if (q) p.set("q", q);
    fin.get("/transactions?" + p).then(setRows);
  };
  const [allMonths, setAllMonths] = useState([]);
  useEffect(() => {
    fin.get("/categories").then(setCats);
    fin.get("/transactions").then((r) => setAllMonths([...new Set(r.map((t) => String(t.date).slice(0, 7)))].sort()));
  }, []);
  useEffect(load, [category, month]); // eslint-disable-line react-hooks/exhaustive-deps
  const fix = async (t, c) => {
    const r = await fin.patch(`/transactions/${t.id}/category`, { category: c, apply_to_merchant: true });
    setMsg(`✅ Updated ${r.updated} transaction(s) from “${t.merchant || t.description}” to ${c}. Future uploads will remember this.`);
    load();
    onChange?.();
  };
  const setFilter = (k, v) => { const n = Object.fromEntries(sp); if (v) n[k] = v; else delete n[k]; setSp(n); };
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Transactions 🧾</h1><p className="muted">Cleaned, de-duplicated and categorised. Fix any category and FinLens applies it to that merchant from now on.</p></div></div>
      <div className="card row reveal">
        <select value={category} onChange={(e) => setFilter("category", e.target.value)}>
          <option value="">All categories</option>
          {[...cats.spending, ...cats.credit.filter((c) => c !== "Uncertain")].map((c) => <option key={c}>{c}</option>)}
        </select>
        <select value={month} onChange={(e) => setFilter("month", e.target.value)}>
          <option value="">All months</option>{allMonths.map((m) => <option key={m}>{m}</option>)}
        </select>
        <input placeholder="🔎 Search description" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
        <button className="subtle" onClick={load}>Search</button>
        {rows && <span className="pill">{rows.length} shown</span>}
      </div>
      {msg && <div className="notice info">{msg}</div>}
      <div className="card reveal" style={{ padding: 0 }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Description</th><th>Need / want</th><th>Category</th><th className="r">Amount</th><th className="r">Balance</th><th>Flags</th></tr></thead>
            <tbody>
              {rows?.map((t) => (
                <tr key={t.id} style={{ opacity: t.excluded ? 0.5 : 1 }}>
                  <td className="num muted">{t.date}</td>
                  <td><b>{t.merchant}</b><div className="muted small">{t.description}{t.time ? ` · ${t.time}` : ""}{t.source && t.source !== "statement" ? ` · ${t.source}` : ""}</div></td>
                  <td>{t.type === "debit" && nat[t.id] && <button className="nature-btn" title={`${nat[t.id].reason}. Click to change for this merchant.`} onClick={() => flip(t)}><NaturePill n={nat[t.id].nature} reason={nat[t.id].reason} /></button>}</td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <span style={{ width: 10, height: 10, borderRadius: 4, background: CAT_COLORS[t.category] || "#00a676" }} />
                      <select value={t.category} onChange={(e) => fix(t, e.target.value)} style={{ padding: "4px 8px", fontSize: 12, borderColor: t.category === "Uncertain" ? "var(--saffron)" : undefined }}>
                        {(t.type === "credit" ? cats.credit : cats.spending).map((c) => <option key={c}>{c}</option>)}
                        {![...cats.credit, ...cats.spending].includes(t.category) && <option>{t.category}</option>}
                      </select>
                    </div>
                    {t.subcategory && <div className="muted small">{CAT_EMOJI[t.category]} {t.subcategory}</div>}
                  </td>
                  <td className="r num" style={{ fontWeight: 700, color: t.type === "credit" ? "var(--emerald)" : "var(--ink)" }}>{t.type === "credit" ? "+" : "−"}{inr(t.amount)}</td>
                  <td className="r num muted">{t.balance != null ? inr(t.balance) : ""}</td>
                  <td className="small">
                    {t.category_source === "user" && <span className="pill green">you set</span>} {t.status === "pending" && <span className="pill amber">pending</span>} {t.excluded && <span className="pill">netted out</span>}
                    {t.credit_kind && t.credit_kind !== "income" && t.type === "credit" && <span className="pill blue">not income</span>}
                    {t.note && <div className="muted">{t.note}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <UploadCard onUploaded={() => { load(); onChange?.(); }} />
    </div>
  );
}
