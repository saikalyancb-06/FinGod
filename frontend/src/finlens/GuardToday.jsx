import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fin, inr } from "../api.js";
import { SpeakButton } from "../voice.jsx";
import { useGuard } from "./GuardContext.jsx";
import { Verdict } from "./SpendGuard.jsx";

// Home card: today's traffic light, where the money went, and a "before you pay" check.
export default function GuardToday() {
  const g = useGuard();
  const [d, setD] = useState(null);
  const [chk, setChk] = useState({ item: "", price: "" });
  const [v, setV] = useState(null);
  useEffect(() => { fin.get("/guard/today").then(setD).catch(() => setD(null)); }, [g.version]);
  if (!d || d.empty) return null;
  const dl = d.daily;
  const col = { green: "#00a676", amber: "#ffb020", red: "#ef476f", grey: "#9aa9ad" }[dl.color];
  return (
    <div className="card reveal guard-today" style={{ borderTop: `5px solid ${col}` }}>
      <div className="grid" style={{ gridTemplateColumns: "auto minmax(0,1fr) minmax(0,1fr)", gap: 18, alignItems: "center" }}>
        <div className="guard-ring" style={{ width: 112, height: 112, background: `conic-gradient(${col} ${Math.min(100, dl.pct || 0) * 3.6}deg, #f1ebe2 0)` }}>
          <div className="guard-ring-in"><b className="num" style={{ fontSize: 18 }}>{inr(dl.spent)}</b><span className="small muted">{dl.limit != null ? `of ${inr(dl.limit)}` : "today"}</span></div>
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}><h3 style={{ margin: 0 }}>🚦 Today</h3>
            <span className={`pill ${dl.color === "red" ? "red" : dl.color === "amber" ? "amber" : "green"}`}>{dl.color === "red" ? "over today's amount" : dl.color === "amber" ? "getting close" : "comfortable"}</span>
            {d.alerts?.some((a) => a.status === "new") && <span className="pill red">{d.alerts.filter((a) => a.status === "new").length} new alerts</span>}</div>
          <p className="small" style={{ margin: "6px 0" }}>{d.explain.speech}</p>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {!d.explain.empty && <SpeakButton text={d.explain.speech} />}
            <Link className="btn small ghost" to="/finlens/guard">Open Spend Guard →</Link>
            {d.jar.pending > 0 && <Link className="pill green" to="/finlens/guard">🫙 {inr(d.jar.pending)} waiting for your jar</Link>}
            {d.wishlist_due > 0 && <Link className="pill blue" to="/finlens/guard?tab=wishlist">🧘 {d.wishlist_due} to decide</Link>}
          </div>
        </div>
        <form onSubmit={async (e) => { e.preventDefault(); setV({ ...(await fin.post("/guard/check", { item: chk.item, price: +chk.price })), item: chk.item, price: +chk.price }); }} className="stack" style={{ gap: 6 }}>
          <b className="small">🤔 About to buy something?</b>
          <div className="row" style={{ gap: 6 }}>
            <input placeholder="What" value={chk.item} onChange={(e) => setChk({ ...chk, item: e.target.value })} required style={{ flex: 2 }} />
            <input type="number" min="1" placeholder="₹" value={chk.price} onChange={(e) => setChk({ ...chk, price: e.target.value })} required style={{ width: 90 }} />
            <button className="small">Check</button>
          </div>
          {v && <Verdict v={v} />}
        </form>
      </div>
    </div>
  );
}
