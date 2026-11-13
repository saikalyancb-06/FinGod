import { useEffect, useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inst } from "../api.js";
import { Skeleton } from "../ui.jsx";

const COLORS = ["#1e9bf0", "#0fb5ae", "#00a676", "#7cc243", "#ffb020", "#ff9233", "#ff6b4a", "#ef476f", "#0c2340", "#8a9ba0"];
const NAMES = {
  validation_ibm: ["IBM HI-Medium validation (20%)", "Same world as training, unseen accounts"],
  validation_inr: ["INR scenarios validation (20%)", "App domain, unseen accounts"],
  "heldout_HI-Small": ["IBM HI-Small", "Separate generated world, never trained on"],
  "heldout_LI-Small": ["IBM LI-Small", "Separate world, low illicit ratio (hardest)"],
  heldout_synthetic_INR_seed2: ["Unseen INR scenario", "Seed 2, never trained on"],
  app_demo_synthetic_INR: ["App demo file", "The synthetic file you upload in the demo"],
};
const fmt = (v, d = 3) => (v == null || Number.isNaN(v) ? "—" : typeof v === "number" ? v.toFixed(d) : v);

export default function Model() {
  const [m, setM] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { inst.get("/meta").then(setM).catch((e) => setErr(e.message)); }, []);
  if (err) return <div className="error">{err}</div>;
  if (!m) return <Skeleton />;
  const mm = m.model_metrics;
  if (!mm) return <div className="card">No trained model found. Run <code>python -m ml.train_mule_ibm</code>.</div>;
  const results = Object.entries(mm.results || {});
  const imp = (mm.feature_importance || []).slice(0, 10).map(([k, v]) => ({ k, v }));
  const pr = results.map(([k, r]) => ({ k: (NAMES[k] || [k])[0], model: r.xgboost?.pr_auc, random: r.random_baseline_pr_auc, lr: r.logistic_regression?.pr_auc }));
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Model &amp; validation 🧪</h1>
        <p className="muted">{mm.model_version} · trained {mm.trained_at} · {mm.trees} trees · alert threshold {fmt(mm.threshold, 2)}</p></div></div>

      <div className="grid g3 stagger">
        <div className="card hero stat"><div className="label">Training data</div><div className="value" style={{ fontSize: 22 }}>31.9M transactions</div>
          <div className="sub">{mm.training_data}</div></div>
        <div className="card stat c-sky"><div className="label">Label</div><div className="value" style={{ fontSize: 18 }}>Laundering-involved account</div><div className="sub">{mm.label}</div></div>
        <div className="card stat c-emerald"><div className="label">Features</div><div className="value" style={{ fontSize: 22 }}>{mm.features?.length || 0} scale-free</div>
          <div className="sub">Same vectorised code scores uploads in the app (no train/serve skew)</div></div>
      </div>

      <div className="card reveal">
        <h3>Results on data the model never trained on</h3>
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table>
            <thead><tr><th>Evaluation set</th><th className="r">Accounts</th><th className="r">Positives</th><th className="r">PR-AUC</th><th className="r">Random</th><th className="r">LogReg</th><th className="r">ROC-AUC</th><th className="r">Precision@100</th><th className="r">Recall @thr</th><th className="r">FPR @thr</th></tr></thead>
            <tbody>{results.map(([k, r]) => (
              <tr key={k}>
                <td><b>{(NAMES[k] || [k])[0]}</b><div className="small muted">{(NAMES[k] || [null, ""])[1]}</div></td>
                <td className="r num">{r.accounts?.toLocaleString("en-IN")}</td>
                <td className="r num">{r.positives?.toLocaleString("en-IN")}</td>
                <td className="r num"><b>{fmt(r.xgboost?.pr_auc)}</b></td>
                <td className="r num muted">{fmt(r.random_baseline_pr_auc, 4)}</td>
                <td className="r num">{fmt(r.logistic_regression?.pr_auc)}</td>
                <td className="r num">{fmt(r.xgboost?.roc_auc)}</td>
                <td className="r num">{fmt(r.xgboost?.precision_at_100, 2)}</td>
                <td className="r num">{fmt(r.xgboost?.at_threshold?.recall, 2)}</td>
                <td className="r num">{fmt(r.xgboost?.at_threshold?.false_positive_rate, 4)}</td>
              </tr>))}</tbody>
          </table>
        </div>
        <p className="small muted">PR-AUC is the main metric because laundering accounts are rare (accuracy would be misleading). "Random" is what a coin-flip ranking scores.</p>
      </div>

      <div className="grid g2 stagger">
        <div className="card">
          <h3>PR-AUC vs random baseline</h3>
          <div style={{ height: 300 }}>
            <ResponsiveContainer>
              <BarChart data={pr} layout="vertical" margin={{ left: 40 }}>
                <XAxis type="number" domain={[0, 1]} /><YAxis type="category" dataKey="k" width={170} tick={{ fontSize: 11 }} /><Tooltip />
                <Bar dataKey="model" name="XGBoost" fill="#1e9bf0" radius={[0, 6, 6, 0]} />
                <Bar dataKey="lr" name="Logistic regression" fill="#ffb020" radius={[0, 6, 6, 0]} />
                <Bar dataKey="random" name="Random" fill="#cbd5d8" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h3>Top features</h3>
          <div style={{ height: 300 }}>
            <ResponsiveContainer>
              <BarChart data={imp} layout="vertical" margin={{ left: 40 }}>
                <XAxis type="number" hide /><YAxis type="category" dataKey="k" width={170} tick={{ fontSize: 11 }} /><Tooltip />
                <Bar dataKey="v" radius={[0, 8, 8, 0]}>{imp.map((r, i) => <Cell key={r.k} fill={COLORS[i % COLORS.length]} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="card reveal">
        <h3>How alerts are prioritised (scores are never averaged)</h3>
        <table><tbody>
          <tr><td><span className="pill high">high</span></td><td>ML score ≥ threshold <b>and</b> ≥ 2 independent rule families</td></tr>
          <tr><td><span className="pill medium">medium</span></td><td>ML score ≥ threshold <b>or</b> ≥ 2 rule families</td></tr>
          <tr><td><span className="pill low">low</span></td><td>exactly 1 rule family and ML score ≥ half the threshold</td></tr>
        </tbody></table>
        <p className="small muted" style={{ marginBottom: 0 }}>IBM AMLworld is a large-scale simulation; no public real-bank AML data exists. Thresholds must be re-validated on authorised institutional data. {m.positioning}</p>
      </div>
    </div>
  );
}
