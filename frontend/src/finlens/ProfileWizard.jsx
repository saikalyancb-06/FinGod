import { useEffect, useState } from "react";
import { fin, inr } from "../api.js";

const STEPS = [
  { key: "you", title: "About you", emoji: "🙋" },
  { key: "money", title: "Money coming in", emoji: "💵" },
  { key: "living", title: "Where you live & fixed costs", emoji: "🏠" },
  { key: "goal", title: "Your goal", emoji: "🎯" },
  { key: "taste", title: "Food & shopping", emoji: "🍛" },
  { key: "credit", title: "Credit (optional)", emoji: "🏦" },
];

function Chips({ options, value, onChange, multi = false }) {
  const isOn = (k) => (multi ? (value || []).includes(k) : value === k);
  const toggle = (k) => onChange(multi ? (isOn(k) ? value.filter((x) => x !== k) : [...(value || []), k]) : k);
  return (
    <div className="row" style={{ gap: 8 }}>
      {Object.entries(options).map(([k, label]) => (
        <button type="button" key={k} onClick={() => toggle(k)} className="small"
          style={isOn(k) ? {} : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }}>{label}</button>
      ))}
    </div>
  );
}

const Num = ({ label, k, p, set, hint, prefix = "₹" }) => (
  <label className="field">{label}
    <div style={{ position: "relative" }}>
      {prefix && <span style={{ position: "absolute", left: 12, top: 10, color: "var(--muted)" }}>{prefix}</span>}
      <input type="number" min="0" value={p[k] || ""} placeholder="0" style={{ paddingLeft: prefix ? 26 : 12, width: "100%" }}
        onChange={(e) => set({ [k]: e.target.value === "" ? 0 : +e.target.value })} />
    </div>
    {hint && <span className="small muted" style={{ fontWeight: 500 }}>{hint}</span>}
  </label>
);

export default function ProfileWizard({ onDone, compact = false }) {
  const [opts, setOpts] = useState(null);
  const [p, setP] = useState(null);
  const [step, setStep] = useState(0);
  const [goal, setGoal] = useState({ name: "", target: "", saved: "", target_date: "" });
  const [saving, setSaving] = useState(false);
  useEffect(() => { fin.get("/profile").then((r) => { setOpts(r.options); setP(r.profile); }); }, []);
  if (!p || !opts) return <div className="skeleton" />;
  const set = (patch) => setP({ ...p, ...patch });
  const income = ["pocket_money", "salary", "stipend", "part_time", "scholarship", "other_income"].reduce((s, k) => s + (+p[k] || 0), 0);
  const fixed = ["rent", "fees_monthly", "emi", "phone_internet", "transport_pass"].reduce((s, k) => s + (+p[k] || 0), 0);
  const isStudent = ["student", "both"].includes(p.occupation);
  const save = async (finish) => {
    setSaving(true);
    try {
      await fin.put("/profile", { ...p, completed: finish ? true : p.completed });
      if (finish && goal.name && +goal.target > 0) {
        await fin.post("/goals", { name: goal.name, target: +goal.target, saved: +goal.saved || 0, target_date: goal.target_date || null });
      }
      if (finish) onDone?.();
    } finally { setSaving(false); }
  };
  const s = STEPS[step];
  const cuisines = Object.fromEntries(opts.cuisines.map((c) => [c, c]));
  return (
    <div className={compact ? "stack" : "card stack reveal"}>
      <div className="row between">
        <h3 style={{ margin: 0 }}><span className="chip-ico" style={{ background: "var(--mint-bg)" }}>{s.emoji}</span>{s.title}</h3>
        <span className="pill">{step + 1} / {STEPS.length}</span>
      </div>
      <div className="progress" style={{ height: 6 }}><i style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></div>

      {s.key === "you" && (<>
        <label className="field">What do you do?<Chips options={opts.occupations} value={p.occupation} onChange={(v) => set({ occupation: v })} /></label>
        <div className="grid g3" style={{ gap: 12 }}>
          <Num label="Age" k="age" p={p} set={set} prefix="" />
          {isStudent && <label className="field">Course / year<input value={p.course} placeholder="e.g. B.E. CSE, 2nd year" onChange={(e) => set({ course: e.target.value })} /></label>}
          {!isStudent && <Num label="Years working" k="years_working" p={p} set={set} prefix="" />}
        </div>
        {p.occupation === "working" && <label className="row small"><input type="checkbox" checked={!!p.self_employed} onChange={(e) => set({ self_employed: e.target.checked })} /> I'm self-employed / freelancing</label>}
        <label className="field">What do you most want help with?<Chips multi options={opts.priorities} value={p.priorities} onChange={(v) => set({ priorities: v })} /></label>
      </>)}

      {s.key === "money" && (<>
        <p className="muted small" style={{ margin: 0 }}>Monthly amounts. FinLens also detects income from your statement and will tell you if they differ.</p>
        <div className="grid g3" style={{ gap: 12 }}>
          {isStudent && <Num label="Pocket money / allowance" k="pocket_money" p={p} set={set} />}
          {p.occupation !== "student" && <Num label="Salary (take-home)" k="salary" p={p} set={set} />}
          {isStudent && <Num label="Stipend / internship" k="stipend" p={p} set={set} />}
          <Num label="Part-time / freelance" k="part_time" p={p} set={set} />
          {isStudent && <Num label="Scholarship (per month)" k="scholarship" p={p} set={set} />}
          <Num label="Other income" k="other_income" p={p} set={set} />
          <label className="field">Money usually arrives on day<input type="number" min="1" max="31" value={p.money_day || ""} placeholder="e.g. 1" onChange={(e) => set({ money_day: e.target.value ? +e.target.value : null })} /></label>
        </div>
        <div className="notice info">Total coming in: <b>{inr(income)}</b> / month</div>
      </>)}

      {s.key === "living" && (<>
        <label className="field">Living situation<Chips options={opts.living} value={p.living} onChange={(v) => set({ living: v })} /></label>
        <div className="grid g2" style={{ gap: 12 }}>
          <label className="field">City<input value={p.city} onChange={(e) => set({ city: e.target.value })} /></label>
          <label className="field">Area / locality (for nearby food search)<input value={p.area} placeholder="e.g. Koramangala" onChange={(e) => set({ area: e.target.value })} /></label>
        </div>
        <div className="grid g3" style={{ gap: 12 }}>
          <Num label={p.living === "hostel" ? "Hostel / mess fee" : "Rent / PG"} k="rent" p={p} set={set} />
          {isStudent && <Num label="College fees (per month)" k="fees_monthly" p={p} set={set} hint="Yearly fee ÷ 12" />}
          <Num label="Loan EMIs" k="emi" p={p} set={set} />
          <Num label="Phone + internet" k="phone_internet" p={p} set={set} />
          <Num label="Travel pass" k="transport_pass" p={p} set={set} />
        </div>
        <div className="notice info">Fixed costs: <b>{inr(fixed)}</b> · left after fixed costs: <b>{inr(income - fixed)}</b> / month</div>
      </>)}

      {s.key === "goal" && (<>
        <p className="muted small" style={{ margin: 0 }}>FinLens plans around this: budgets, recommendations and answers will keep it on schedule.</p>
        <div className="grid g2" style={{ gap: 12 }}>
          <label className="field">What are you saving for?<input value={goal.name} placeholder="Laptop, trip to Goa, emergency fund…" onChange={(e) => setGoal({ ...goal, name: e.target.value })} /></label>
          <label className="field">By when?<input type="date" value={goal.target_date} onChange={(e) => setGoal({ ...goal, target_date: e.target.value })} /></label>
          <label className="field">Target ₹<input type="number" min="0" value={goal.target} onChange={(e) => setGoal({ ...goal, target: e.target.value })} /></label>
          <label className="field">Already saved ₹<input type="number" min="0" value={goal.saved} onChange={(e) => setGoal({ ...goal, saved: e.target.value })} /></label>
        </div>
        {goal.target && goal.target_date && (() => {
          const months = Math.max(1, (new Date(goal.target_date) - new Date()) / (30.44 * 864e5));
          const need = Math.max(0, (+goal.target - (+goal.saved || 0)) / months);
          const free = income - fixed;
          return <div className={`notice ${need <= free * 0.5 ? "info" : ""}`}>You'd need about <b>{inr(need)}/month</b> for {months.toFixed(1)} months{free > 0 ? `, which is ${Math.round(need / free * 100)}% of what's left after fixed costs` : ""}.{need > free * 0.5 ? " That's ambitious: consider a later date." : ""}</div>;
        })()}
        <div className="grid g2" style={{ gap: 12 }}>
          <Num label="Monthly savings target (overall)" k="savings_target" p={p} set={set} />
          <Num label="Emergency buffer to always keep" k="emergency_buffer" p={p} set={set} />
        </div>
      </>)}

      {s.key === "taste" && (<>
        <label className="field">Diet<Chips options={opts.diets} value={p.diet} onChange={(v) => set({ diet: v })} /></label>
        <label className="field">Favourite food<Chips multi options={cuisines} value={p.cuisines} onChange={(v) => set({ cuisines: v })} /></label>
        <div className="grid g3" style={{ gap: 12 }}>
          <Num label="Usual budget per meal" k="food_budget_per_meal" p={p} set={set} />
          <label className="field">Clothing size<input value={p.clothing_size} placeholder="S / M / L / XL" onChange={(e) => set({ clothing_size: e.target.value })} /></label>
          <label className="field">Shoe size (UK)<input value={p.shoe_size} placeholder="e.g. 9" onChange={(e) => set({ shoe_size: e.target.value })} /></label>
          <label className="field">Style<input value={p.style} placeholder="casual, sporty, formal…" onChange={(e) => set({ style: e.target.value })} /></label>
        </div>
      </>)}

      {s.key === "credit" && (<>
        <p className="muted small" style={{ margin: 0 }}>Only used for the lender-style approval estimate. Skip if you don't know; most students have no score yet.</p>
        <div className="grid g2" style={{ gap: 12 }}>
          <Num label="CIBIL / credit score (300–900)" k="cibil_score" p={p} set={set} prefix="" />
          <Num label="Years of credit history" k="credit_history_years" p={p} set={set} prefix="" />
        </div>
      </>)}

      <div className="row between">
        <button type="button" className="subtle" disabled={step === 0} onClick={() => setStep(step - 1)}>← Back</button>
        <div className="row">
          {compact && <button type="button" className="ghost" disabled={saving} onClick={() => save(false)}>Save</button>}
          {step < STEPS.length - 1
            ? <button type="button" onClick={() => { setStep(step + 1); save(false); }}>Next →</button>
            : <button type="button" className="warm" disabled={saving || !p.occupation} onClick={() => save(true)}>{saving ? "Saving…" : "Finish ✨"}</button>}
        </div>
      </div>
      {!p.occupation && step === STEPS.length - 1 && <div className="small muted">Pick what you do (step 1) to finish.</div>}
    </div>
  );
}
