import { useEffect, useState } from "react";
import { fin, inr } from "../api.js";
import { Skeleton } from "../ui.jsx";

const EMOJIS = ["🙂", "😎", "🦊", "🐼", "🌻", "⚡", "🎧", "🍕"];
const AVA = ["#ff6b4a", "#00a676", "#1e9bf0", "#ffb020", "#e8467c", "#7a5af8", "#13b5b1", "#ff9233"];

function Avatar({ f, size = 38 }) {
  return <div style={{ width: size, height: size, borderRadius: size / 2.6, background: AVA[f.id % AVA.length], display: "grid", placeItems: "center", fontSize: size * 0.5, color: "#fff", flex: "none" }}>{f.emoji || f.name[0]}</div>;
}

function SplitForm({ friends, prefill, onDone, onCancel }) {
  const [title, setTitle] = useState(prefill?.merchant || prefill?.description || "");
  const [total, setTotal] = useState(prefill?.amount || "");
  const [payer, setPayer] = useState(0);
  const [who, setWho] = useState(prefill?.friend_id ? [prefill.friend_id] : []);
  const [mode, setMode] = useState("even");
  const [custom, setCustom] = useState({});
  const [err, setErr] = useState("");
  const people = ["me", ...who.map(String)];
  const evenShare = total && people.length > 1 ? Number(total) / people.length : 0;
  const customSum = Object.entries(custom).filter(([k]) => people.includes(k)).reduce((a, [, v]) => a + Number(v || 0), 0);
  const submit = async (e) => {
    e.preventDefault(); setErr("");
    try {
      const body = { title, total: Number(total), paid_by: Number(payer), with_friends: who, txn_id: prefill?.txn_id || null };
      if (mode === "custom") body.shares = Object.fromEntries(people.map((p) => [p, Number(custom[p] || 0)]));
      await fin.post("/split/entries", body);
      onDone();
    } catch (x) { setErr(x.message); }
  };
  return (
    <form className="card pop" onSubmit={submit} style={{ borderTop: "4px solid var(--emerald)" }}>
      <h3 style={{ marginTop: 0 }}>🧾 {prefill ? "Split this payment" : "Add a shared expense"}</h3>
      {prefill && <div className="small muted" style={{ marginBottom: 8 }}>From your statement: {prefill.description} · {prefill.date}</div>}
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr", gap: 10 }}>
        <label className="small">What was it?<input value={title} onChange={(e) => setTitle(e.target.value)} required placeholder="Dinner at Truffles" /></label>
        <label className="small">Total (₹)<input type="number" min="1" step="0.01" value={total} onChange={(e) => setTotal(e.target.value)} required disabled={!!prefill} /></label>
      </div>
      <div className="small" style={{ margin: "10px 0 4px" }}>Shared with</div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {friends.map((f) => {
          const on = who.includes(f.id);
          return <button type="button" key={f.id} className="small" onClick={() => setWho(on ? who.filter((x) => x !== f.id) : [...who, f.id])}
            style={on ? { background: AVA[f.id % AVA.length] } : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }}>{f.emoji} {f.name}</button>;
        })}
        {!friends.length && <span className="small muted">Add a friend first (left).</span>}
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
        <label className="small">Who paid?
          <select value={payer} onChange={(e) => setPayer(e.target.value)} disabled={!!prefill && prefill.type === "debit"}>
            <option value={0}>You</option>{friends.filter((f) => who.includes(f.id)).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select></label>
        <label className="small">How to split?
          <select value={mode} onChange={(e) => setMode(e.target.value)}><option value="even">Evenly</option><option value="custom">Custom amounts</option></select></label>
      </div>
      {who.length > 0 && total && (mode === "even" ? (
        <div className="notice small" style={{ marginTop: 10 }}>Everyone pays <b>{inr(evenShare)}</b>. {Number(payer) === 0 ? `Friends will owe you ${inr(Number(total) - evenShare)} in total.` : `You'll owe ${inr(evenShare)}.`} Only your share counts in your spending.</div>
      ) : (
        <div className="stack" style={{ gap: 6, marginTop: 10 }}>
          {people.map((p) => {
            const f = friends.find((x) => String(x.id) === p);
            return <label key={p} className="row between small"><span>{p === "me" ? "You" : `${f.emoji} ${f.name}`}</span>
              <input type="number" min="0" step="0.01" style={{ maxWidth: 140 }} value={custom[p] || ""} onChange={(e) => setCustom({ ...custom, [p]: e.target.value })} /></label>;
          })}
          <div className={`small ${Math.abs(customSum - Number(total)) > 1 ? "error" : "muted"}`}>Shares add to {inr(customSum)} of {inr(Number(total))}</div>
        </div>))}
      {err && <div className="error small" style={{ marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ gap: 8, marginTop: 12 }}>
        <button disabled={!who.length}>Save split</button>
        <button type="button" className="ghost" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function SettleForm({ friend, prefill, onDone, onCancel }) {
  const owes = friend.balance > 0;
  const [amount, setAmount] = useState(prefill?.amount || Math.abs(friend.balance) || "");
  const [dir, setDir] = useState(prefill ? (prefill.type === "credit" ? "they_paid" : "i_paid") : owes ? "they_paid" : "i_paid");
  const [err, setErr] = useState("");
  const settled = Math.abs(friend.balance) < 0.5;
  const [earlier, setEarlier] = useState(settled);
  const submit = async (e) => {
    e.preventDefault();
    try { await fin.post("/split/settle", { friend_id: friend.id, amount: Number(amount), direction: dir, txn_id: prefill?.txn_id || null, earlier }); onDone(); } catch (x) { setErr(x.message); }
  };
  return (
    <form className="card pop" onSubmit={submit} style={{ borderTop: "4px solid var(--sky)" }}>
      <h3 style={{ marginTop: 0 }}>🤝 Settle up with {friend.name}</h3>
      <div className="row" style={{ gap: 6 }}>
        <button type="button" className="small" onClick={() => setDir("they_paid")} style={dir === "they_paid" ? {} : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }}>{friend.name} paid me</button>
        <button type="button" className="small" onClick={() => setDir("i_paid")} style={dir === "i_paid" ? {} : { background: "#fff", color: "var(--ink)", boxShadow: "inset 0 0 0 1.5px var(--line)" }}>I paid {friend.name}</button>
      </div>
      <label className="small" style={{ display: "block", marginTop: 10 }}>Amount (₹)<input type="number" min="1" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required disabled={!!prefill} /></label>
      <label className="row small" style={{ gap: 8, marginTop: 8, cursor: "pointer" }}>
        <input type="checkbox" checked={earlier} onChange={(e) => setEarlier(e.target.checked)} style={{ width: 18 }} />
        {dir === "they_paid" ? `${friend.name} was paying me back for something I covered earlier` : `It was my share of something ${friend.name} paid earlier`}
      </label>
      {earlier && <div className="small muted">{dir === "they_paid" ? "You'll stay settled with " + friend.name + ", and this money won't count as income." : "It counts as your own spending; nothing is left to settle."}</div>}
      {prefill && <div className="small muted">Linked to: {prefill.description} · {prefill.date}. It won't be counted as income or spending.</div>}
      {err && <div className="error small">{err}</div>}
      <div className="row" style={{ gap: 8, marginTop: 12 }}><button>Record</button><button type="button" className="ghost" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

export default function Split() {
  const [d, setD] = useState(null);
  const [name, setName] = useState("");
  const [form, setForm] = useState(null); // {type: "split"|"settle", prefill, friend}
  const [err, setErr] = useState("");
  const load = () => fin.get("/split").then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  if (err && !d) return <div className="error">{err}</div>;
  if (!d) return <Skeleton rows={3} />;
  const addFriend = async (e, nm = name) => {
    e?.preventDefault();
    if (!nm.trim()) return null;
    const f = await fin.post("/split/friends", { name: nm.trim(), emoji: EMOJIS[Math.floor(Math.random() * EMOJIS.length)] });
    setName(""); await load(); return f;
  };
  const useSuggestion = async (s) => {
    let fid = s.friend_id;
    if (!fid && s.person && s.kind !== "expense") { const f = await addFriend(null, s.person); fid = f?.id; }
    const fresh = await fin.get("/split"); setD(fresh);
    if (s.kind === "expense") setForm({ type: "split", prefill: s });
    else setForm({ type: "settle", prefill: s, friend: fresh.friends.find((f) => f.id === fid) });
  };
  const done = () => { setForm(null); load(); };
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Friends &amp; splits 🤝</h1>
        <p className="muted">Split shared bills, track who owes whom, and settle up. Split payments count only <b>your share</b> in your spending, goals and safe-to-spend.</p></div></div>

      <div className="grid g3 stagger">
        <div className="card stat lift" style={{ background: "#e3f8ef" }}><div className="label">Friends owe you</div><div className="value" style={{ color: "var(--emerald)" }}>{inr(d.owed_to_you)}</div></div>
        <div className="card stat lift" style={{ background: "#fff1ec" }}><div className="label">You owe</div><div className="value" style={{ color: "var(--coral)" }}>{inr(d.you_owe)}</div></div>
        <div className="card stat lift" style={{ background: "#e4f3fe" }}><div className="label">Net</div><div className="value" style={{ color: "var(--sky)" }}>{d.owed_to_you - d.you_owe >= 0 ? "+" : "−"}{inr(Math.abs(d.owed_to_you - d.you_owe))}</div></div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1.4fr)", gap: 16, alignItems: "start" }}>
        <div className="stack">
          <div className="card lift">
            <h3>👥 Friends</h3>
            <form className="row" style={{ gap: 6 }} onSubmit={addFriend}><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Add a friend's name" /><button className="small">Add</button></form>
            <div className="stack" style={{ gap: 8, marginTop: 12 }}>
              {d.friends.map((f) => (
                <div key={f.id} className="row between friend-row">
                  <div className="row" style={{ gap: 10 }}><Avatar f={f} /><div><b>{f.name}</b>
                    <div className="small" style={{ color: f.balance > 0.5 ? "var(--emerald)" : f.balance < -0.5 ? "var(--coral)" : "var(--muted)" }}>
                      {f.balance > 0.5 ? `owes you ${inr(f.balance)}` : f.balance < -0.5 ? `you owe ${inr(-f.balance)}` : "all settled ✓"}</div></div></div>
                  <div className="row" style={{ gap: 4 }}>
                    {Math.abs(f.balance) > 0.5 && <button className="small" onClick={() => setForm({ type: "settle", friend: f })}>Settle</button>}
                    {f.entries === 0 && <button className="small ghost" title="Remove" onClick={async () => { await fin.del(`/split/friends/${f.id}`); load(); }}>×</button>}
                  </div>
                </div>))}
              {!d.friends.length && <p className="muted small">No friends yet. Add the people you share bills with.</p>}
            </div>
          </div>
          <button onClick={() => setForm({ type: "split" })} disabled={!d.friends.length}>＋ Add a shared expense</button>
        </div>

        <div className="stack">
          {form?.type === "split" && <SplitForm friends={d.friends} prefill={form.prefill} onDone={done} onCancel={() => setForm(null)} />}
          {form?.type === "settle" && form.friend && <SettleForm friend={form.friend} prefill={form.prefill} onDone={done} onCancel={() => setForm(null)} />}
          {form?.type === "settle" && !form.friend && <div className="notice small">Pick or add the friend first, then use “Settle”.</div>}

          {d.paybacks?.map((p) => (
            <div key={p.txn_id} className="card pop payback">
              <div className="row between" style={{ alignItems: "flex-start", gap: 12 }}>
                <h3 style={{ margin: 0 }}>🤝 Were they paying you back?</h3>
                <span className="pill blue">{p.split_ways}-way split?</span>
              </div>
              <p style={{ margin: "8px 0" }}>{p.reason}</p>
              <div className="payback-flow">
                <div className="pb-node out"><div className="small muted">You paid · {p.date}</div><b>{p.merchant}</b><div className="num big">{inr(p.amount)}</div></div>
                <div className="pb-arrow">→</div>
                <div className="stack" style={{ gap: 6 }}>
                  {p.credits.map((c) => (
                    <div key={c.txn_id} className="pb-node in"><div className="small muted">{c.date}</div><b>{c.person || "Someone"}</b> <span className="num">+{inr(c.amount)}</span></div>))}
                </div>
                <div className="pb-arrow">=</div>
                <div className="pb-node me"><div className="small muted">Your real share</div><div className="num big">{inr(p.my_share)}</div></div>
              </div>
              <div className="row" style={{ gap: 8, marginTop: 12 }}>
                <button onClick={async () => { await fin.post("/split/payback", { debit_txn_id: p.txn_id, credit_txn_ids: p.credits.map((c) => c.txn_id) }); load(); }}>✓ Yes, they paid me back</button>
                <button className="ghost" onClick={async () => { await fin.post(`/split/payback/${p.txn_id}/dismiss`); load(); }}>No, unrelated</button>
              </div>
              <p className="small muted" style={{ margin: "8px 0 0" }}>Saying yes splits the bill, marks everyone settled, counts only your {inr(p.my_share)} as spending and stops these transfers counting as income.</p>
            </div>))}
          {d.suggestions.length > 0 && (
            <div className="card lift">
              <h3>✨ From your statement</h3>
              <p className="small muted" style={{ marginTop: -4 }}>Payments that look shared, and money sent to or received from people.</p>
              <div className="stack" style={{ gap: 8 }}>
                {d.suggestions.map((s) => (
                  <div key={s.txn_id} className="row between sugg-row">
                    <div style={{ minWidth: 0 }}>
                      <b>{s.type === "credit" ? "📥" : "📤"} {s.merchant || s.description}</b> <span className="num" style={{ color: s.type === "credit" ? "var(--emerald)" : "var(--ink)" }}>{inr(s.amount)}</span>
                      <div className="small muted">{s.date} · {s.reason}</div>
                    </div>
                    <button className="small" onClick={() => useSuggestion(s)}>{s.kind === "expense" ? "Split" : "Record"}</button>
                  </div>))}
              </div>
            </div>)}

          <div className="card lift">
            <h3>📜 Activity</h3>
            {d.entries.map((e) => (
              <div key={e.id} className="row between" style={{ padding: "9px 0", borderBottom: "1px dashed var(--line)" }}>
                <div style={{ minWidth: 0 }}>
                  <b>{e.kind === "settlement" ? "🤝" : "🧾"} {e.title}</b>{e.txn_id && <span className="pill blue" style={{ marginLeft: 6 }}>linked</span>}
                  <div className="small muted">{e.date} · {e.paid_by_name} paid {inr(e.total)}{e.kind === "expense" ? ` · split: ${e.people.join(", ")}` : ""}</div>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  {e.kind === "expense" && <span className="small">your share <b>{inr(e.my_share)}</b></span>}
                  <button className="small ghost" title="Delete" onClick={async () => { await fin.del(`/split/entries/${e.id}`); load(); }}>×</button>
                </div>
              </div>))}
            {!d.entries.length && <p className="muted small">Nothing yet. Split a bill or record a payback to get started.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
