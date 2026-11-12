import { useState } from "react";
import { inr } from "../api.js";

// GitHub-style spending calendar (idea from the team's CredWise "FinanceTracker").
const SCALE = ["#f4efe7", "#ffe3a3", "#ffc061", "#ff9233", "#ff6b4a", "#e2453c"];

export default function Heatmap({ data, period }) {
  const [hover, setHover] = useState(null);
  const map = Object.fromEntries(data.map((d) => [d.date, d]));
  const start = new Date(period[0]);
  start.setDate(start.getDate() - start.getDay()); // back to Sunday
  const end = new Date(period[1]);
  const max = Math.max(1, ...data.map((d) => d.spend));
  const weeks = [];
  for (let d = new Date(start); d <= end; ) {
    const w = [];
    for (let i = 0; i < 7; i++) {
      const key = d.toISOString().slice(0, 10);
      const inRange = d >= new Date(period[0]) && d <= end;
      w.push({ key, inRange, v: map[key] });
      d.setDate(d.getDate() + 1);
    }
    weeks.push(w);
  }
  const level = (v) => (!v ? 0 : Math.min(5, 1 + Math.floor((v.spend / max) * 5)));
  return (
    <div>
      <div style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 4 }}>
        {weeks.map((w, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {i === 0 || new Date(w[0].key).getDate() <= 7 ? <div className="small muted" style={{ height: 14, fontSize: 10 }}>{new Date(w[0].key).toLocaleString("en-IN", { month: "short" })}</div> : <div style={{ height: 14 }} />}
            {w.map((c) => (
              <div key={c.key} onMouseEnter={() => setHover(c)} onMouseLeave={() => setHover(null)}
                style={{ width: 16, height: 16, borderRadius: 5, background: c.inRange ? SCALE[level(c.v)] : "transparent",
                  transition: "transform .15s", transform: hover?.key === c.key ? "scale(1.35)" : "none", cursor: "default",
                  animation: "pop .4s both", animationDelay: `${i * 0.02}s` }} />
            ))}
          </div>
        ))}
      </div>
      <div className="row between small muted" style={{ marginTop: 6 }}>
        <span>{hover?.inRange ? `${new Date(hover.key).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })}: ${hover.v ? `${inr(hover.v.spend)} · ${hover.v.count} txns` : "no spending"}` : "Hover a day to see spending"}</span>
        <span className="row" style={{ gap: 3 }}>less {SCALE.map((c) => <i key={c} style={{ width: 11, height: 11, borderRadius: 3, background: c, display: "inline-block" }} />)} more</span>
      </div>
    </div>
  );
}
