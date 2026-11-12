import { useEffect, useRef, useState } from "react";

// Animated number that counts up when its value changes.
export function CountUp({ value, format = (v) => v, duration = 900 }) {
  const [shown, setShown] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (value === null || value === undefined || Number.isNaN(value)) return;
    const start = performance.now();
    const a = from.current;
    let raf;
    const step = (t) => {
      const k = Math.min(1, (t - start) / duration);
      const e = 1 - Math.pow(1 - k, 3);
      setShown(a + (value - a) * e);
      if (k < 1) raf = requestAnimationFrame(step);
      else from.current = value;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  if (value === null || value === undefined) return <>—</>;
  return <>{format(shown)}</>;
}

export function Blobs() {
  return <div className="blobs" aria-hidden="true"><i /><i /><i /></div>;
}

export function Brand({ name }) {
  return (
    <div className="brand">
      <span className="brand-mark"><i style={{ height: 10 }} /><i style={{ height: 17 }} /><i style={{ height: 24 }} /></span>
      {name}
    </div>
  );
}

export function Stat({ label, value, sub, icon, color = "emerald", format }) {
  return (
    <div className={`card stat lift c-${color}`}>
      {icon && <div className="stat-ico">{icon}</div>}
      <div className="label">{label}</div>
      <div className="value">{typeof value === "number" ? <CountUp value={value} format={format} /> : value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Skeleton({ rows = 3 }) {
  return <div className="stack">{Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton" />)}</div>;
}

export function Typing() {
  return <div className="msg"><div className="avatar">✦</div><div className="bubble typing"><span /><span /><span /></div></div>;
}

// Safe markdown renderer for AI answers (tables, headings, bold, lists) - see Markdown.jsx
export { default as Md } from "./Markdown.jsx";

// Vibrant categorical palette (no purple).
export const CAT_COLORS = {
  Food: "#ff6b4a", Transportation: "#1e9bf0", Shopping: "#ffb020", Entertainment: "#ef476f", Education: "#0fb5ae",
  "Accommodation & Utilities": "#00a676", Healthcare: "#7cc243", "Transfers & Other": "#8a9ba0", Uncertain: "#cbd5d8",
};
export const CAT_EMOJI = {
  Food: "🍛", Transportation: "🚇", Shopping: "🛍️", Entertainment: "🎬", Education: "📚",
  "Accommodation & Utilities": "🏠", Healthcare: "💊", "Transfers & Other": "🔁", Uncertain: "❔",
};
