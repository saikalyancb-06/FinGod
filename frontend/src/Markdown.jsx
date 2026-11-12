// Small, safe Markdown renderer for AI answers (no HTML injection, everything is React text).
// Supports: # headings, **bold**/__bold__, *italic*/_italic_, `code`, bullet + numbered lists,
// | tables |, > quotes, --- rules, and paragraphs. ₹ amounts are highlighted.
import { Fragment } from "react";

const INLINE = /(\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`|₹\s?-?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?(?:\s?(?:k|L|lakh)\b)?)/g;

function inline(text, key = "i") {
  const parts = String(text).split(INLINE).filter((p) => p !== "" && p !== undefined);
  return parts.map((p, i) => {
    const k = `${key}-${i}`;
    if (p.startsWith("***") && p.endsWith("***")) return <b key={k}><i>{inline(p.slice(3, -3), k)}</i></b>;
    if ((p.startsWith("**") && p.endsWith("**")) || (p.startsWith("__") && p.endsWith("__"))) return <b key={k}>{inline(p.slice(2, -2), k)}</b>;
    if (p.length > 2 && ((p.startsWith("*") && p.endsWith("*")) || (p.startsWith("_") && p.endsWith("_")))) return <i key={k}>{inline(p.slice(1, -1), k)}</i>;
    if (p.startsWith("`") && p.endsWith("`")) return <code key={k} className="md-code">{p.slice(1, -1)}</code>;
    if (p.startsWith("₹")) return <span key={k} className="md-money">{p.replace(/\s/g, "")}</span>;
    return <Fragment key={k}>{p}</Fragment>;
  });
}

const isTableRow = (l) => /^\s*\|.*\|\s*$/.test(l);
const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export default function Markdown({ text }) {
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (!ln.trim()) { i++; continue; }
    // table
    if (isTableRow(ln) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(ln);
      const rows = [];
      i += 2;
      while (i < lines.length && isTableRow(lines[i])) { rows.push(cells(lines[i])); i++; }
      out.push(
        <div key={`t${i}`} className="md-table-wrap"><table className="md-table">
          <thead><tr>{head.map((h, j) => <th key={j}>{inline(h, `th${i}${j}`)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `td${i}${ri}${j}`)}</td>)}</tr>)}</tbody>
        </table></div>);
      continue;
    }
    // heading
    const h = ln.match(/^\s*(#{1,4})\s+(.*)$/);
    if (h) { out.push(<div key={`h${i}`} className={`md-h md-h${h[1].length}`}>{inline(h[2].replace(/\*\*/g, ""), `h${i}`)}</div>); i++; continue; }
    // a line that is only bold text acts as a heading too ("**Two practical steps**")
    const bh = ln.match(/^\s*\*\*([^*]+)\*\*:?\s*$/);
    if (bh) { out.push(<div key={`bh${i}`} className="md-h md-h3">{inline(bh[1], `bh${i}`)}</div>); i++; continue; }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(ln)) { out.push(<hr key={`hr${i}`} className="md-hr" />); i++; continue; }
    if (/^\s*>/.test(ln)) {
      const q = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push(<blockquote key={`q${i}`} className="md-quote">{inline(q.join(" "), `q${i}`)}</blockquote>);
      continue;
    }
    // lists
    const isBullet = (l) => /^\s*[-*•+]\s+/.test(l);
    const isNum = (l) => /^\s*\d+[.)]\s+/.test(l);
    if (isBullet(ln) || isNum(ln)) {
      const ordered = isNum(ln);
      const items = [];
      while (i < lines.length && (ordered ? isNum(lines[i]) : isBullet(lines[i]))) {
        let item = lines[i].replace(/^\s*([-*•+]|\d+[.)])\s+/, "");
        i++;
        // continuation lines (indented, not a new item)
        while (i < lines.length && lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && !isBullet(lines[i]) && !isNum(lines[i])) { item += " " + lines[i].trim(); i++; }
        items.push(item);
      }
      const L = ordered ? "ol" : "ul";
      out.push(<L key={`l${i}`} className="md-list">{items.map((it, j) => <li key={j}>{inline(it, `li${i}${j}`)}</li>)}</L>);
      continue;
    }
    // paragraph: merge consecutive plain lines
    const para = [];
    while (i < lines.length && lines[i].trim() && !isTableRow(lines[i]) && !/^\s*(#{1,4}\s|[-*•+]\s|\d+[.)]\s|>)/.test(lines[i]) && !/^\s*\*\*[^*]+\*\*:?\s*$/.test(lines[i])) {
      para.push(lines[i].trim()); i++;
    }
    if (para.length) out.push(<p key={`p${i}`} className="md-p">{inline(para.join(" "), `p${i}`)}</p>);
    else i++;
  }
  return <div className="md">{out}</div>;
}
