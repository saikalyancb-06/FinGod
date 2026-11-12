import { useEffect, useState } from "react";
import { inst } from "../api.js";

export default function Audit({ me }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { inst.get("/audit?limit=300").then(setRows).catch((e) => setErr(e.message)); }, []);
  return (
    <div className="stack">
      <div className="page-head reveal"><div><h1>Audit log 📜</h1><p className="muted">Every login, data view, status change, export and identifier reveal is recorded.</p></div></div>
      {me.role !== "lead" && <div className="notice">Only a lead investigator can view the audit log.</div>}
      {err && me.role === "lead" && <div className="error">{err}</div>}
      {rows && (
        <div className="card reveal" style={{ padding: 0 }}>
          <div className="table-wrap"><table>
            <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i}><td className="num small">{new Date(r.at + "Z").toLocaleString("en-IN")}</td><td><b>{r.actor}</b></td>
                <td><span className={`pill ${r.action.includes("reveal") ? "red" : r.action.includes("status") ? "amber" : "blue"}`}>{r.action}</span></td>
                <td className="num small">{r.target}</td><td className="small muted">{Object.keys(r.detail || {}).length ? JSON.stringify(r.detail) : ""}</td></tr>))}</tbody>
          </table></div>
        </div>)}
    </div>
  );
}
