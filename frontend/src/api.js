// Separate clients and separate tokens: a FinLens session is never sent to the institutional API.
function makeClient(base, tokenKey) {
  const token = () => localStorage.getItem(tokenKey);
  async function req(method, path, body, opts = {}) {
    const headers = {};
    if (token()) headers.Authorization = `Bearer ${token()}`;
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const r = await fetch(base + path, { method, headers, body: payload });
    if (r.status === 401 && !opts.noAuthRedirect) {
      localStorage.removeItem(tokenKey);
      window.dispatchEvent(new Event(`${tokenKey}:logout`));
    }
    const text = await r.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!r.ok) {
      const msg = data?.detail ? (typeof data.detail === "string" ? data.detail : data.detail.map((d) => d.msg).join("; ")) : `Request failed (${r.status})`;
      const err = new Error(msg); err.status = r.status; throw err;
    }
    return data;
  }
  return {
    tokenKey,
    get: (p) => req("GET", p),
    post: (p, b) => req("POST", p, b),
    put: (p, b) => req("PUT", p, b),
    patch: (p, b) => req("PATCH", p, b),
    del: (p) => req("DELETE", p),
    login: (p, b) => req("POST", p, b, { noAuthRedirect: true }),
    setToken: (t) => (t ? localStorage.setItem(tokenKey, t) : localStorage.removeItem(tokenKey)),
    hasToken: () => !!token(),
  };
}

export const fin = makeClient("/x-fin/api", "finlens_token");
export const inst = makeClient("/x-inst/api", "institution_token");

const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
export const inr = (v) => (v === null || v === undefined || Number.isNaN(v) ? "—" : `₹${inrFmt.format(v)}`);
export const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
// Compact axis labels: ₹500, ₹1.5k, ₹12k, ₹1.2L
export const kfmt = (v) => {
  const a = Math.abs(v);
  if (a >= 100000) return `₹${+(v / 100000).toFixed(1)}L`;
  if (a >= 1000) return `₹${+(v / 1000).toFixed(a >= 10000 ? 0 : 1)}k`;
  return `₹${Math.round(v)}`;
};
