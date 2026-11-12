import { useEffect, useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inst } from "../api.js";
import { Skeleton } from "../ui.jsx";

const COLORS = ["#1e9bf0", "#0fb5ae", "#00a676", "#7cc243", "#ffb020", "#ff9233", "#ff6b4a", "#ef476f"];

export default function Model() {
  const [m, setM] = useState(null);
  useEffect(() => { inst.get("/meta").then(setM); }, []);
  if (!m) return <Skeleton />;
  const mm = m.model_metrics;
  if (!mm) return <div className="card">No trained model found. Run <code>python -m ml.train_mule_model</code>.</div>;
  const rows = [["XGBoost (chosen threshold)", mm.xgboost], ["Logistic regression", mm.baseline_logistic_regression], ["Rules only, ≥1 rule", mm["baseline_rules_only(>=1 rule)"]], ["Rules only, ≥2 rules", mm["baseline_rules_only(>=2 rules)"]]];
  const imp = mm.feature_importance.slice(0, 10).map(([k, v]) => ({ k, v }));
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Model &amp; validation 🧪</h1><p className="muted">{mm.model_version} · trained {mm.trained_at}</p></div></div>
      <div className="notice reveal">⚠️ {mm.data}</div>
      <div className="grid g2 stagger">
        <div className="card">
          <h3>Held-out scenario ({mm.heldout_scenario.accounts} accounts, {mm.heldout_scenario.mules} injected mules)</h3>
          <table><thead><tr><th>Model</th><th className="r">PR-AUC</th><th className="r">ROC-AUC</th><th className="r">Precision</th><th className="r">Recall</th><th className="r">FPR</th></tr></thead>
            <tbody>{rows.map(([n, r]) => <tr key={n}><td>{n}</td><td className="r num"><b>{r.pr_auc}</b></td><td className="r num">{r.roc_auc}</td><td className="r num">{r.at_threshold.precision}</td><td className="r num">{r.at_threshold.recall}</td><td className="r num">{r.at_threshold.false_positive_rate}</td></tr>)}</tbody></table>
          <p className="small muted">The threshold ({mm.chosen_threshold}) was chosen on ring-aware out-of-fold predictions from the training scenario only. Accuracy isn't reported because the data is heavily imbalanced.</p>
          <h3 style={{ marginTop: 12 }}>Recall by typology</h3>
          <div className="row">{Object.entries(mm.recall_by_typology).map(([k, v]) => <span key={k} className="pill green">{k}: {v}</span>)}</div>
          <h3 style={{ marginTop: 12 }}>False positives by true account type</h3>
          <div className="row">{Object.entries(mm.false_positives_by_type).map(([k, v]) => <span key={k} className="pill amber">{k}: {v}</span>)}</div>
        </div>
        <div className="card">
          <h3>Top features</h3>
          <div style={{ height: 330 }}>
            <ResponsiveContainer>
              <BarChart data={imp} layout="vertical" margin={{ left: 40 }}>
                <XAxis type="number" hide /><YAxis type="category" dataKey="k" width={160} tick={{ fontSize: 11 }} /><Tooltip />
                <Bar dataKey="v" radius={[0, 8, 8, 0]}>{imp.map((r, i) => <Cell key={r.k} fill={COLORS[i % COLORS.length]} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
      <div className="card reveal">
        <h3>How alerts are prioritised (no score averaging)</h3>
        <table><tbody>
          <tr><td><span className="pill high">high</span></td><td>ML score ≥ threshold <b>and</b> ≥ 2 independent rule families</td></tr>
          <tr><td><span className="pill medium">medium</span></td><td>ML score ≥ threshold <b>or</b> ≥ 2 rule families</td></tr>
          <tr><td><span className="pill low">low</span></td><td>exactly 1 rule family and ML score ≥ half the threshold</td></tr>
        </tbody></table>
        <p className="small muted">{m.positioning}</p>
      </div>
    </div>
  );
}
