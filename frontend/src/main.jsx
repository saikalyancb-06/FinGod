import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import "./styles.css";
import FinLensApp from "./finlens/FinLensApp.jsx";
import InstApp from "./institution/InstApp.jsx";
import { Blobs } from "./ui.jsx";

function Landing() {
  return (
    <div className="landing">
      <Blobs />
      <div className="landing-inner reveal">
        <span className="eyebrow">ZeroHour · Track 1 · Prototype</span>
        <h1>Understand your money.<br /><span className="grad-text">Investigate suspicious money.</span></h1>
        <p className="muted" style={{ fontSize: 16, maxWidth: 620 }}>
          Two independent products. They share code, but never customer data, logins or data stores.
        </p>
        <div className="landing-cards stagger">
          <Link to="/finlens" className="landing-card green">
            <span className="emoji">💸</span>
            <span className="tag">B2C · Individuals</span>
            <h2>FinLens</h2>
            <p>See where your money goes, find better-value food and fashion, and check loan affordability from your own statement.</p>
            <span className="go">Open FinLens →</span>
          </Link>
          <Link to="/institution" className="landing-card blue">
            <span className="emoji">🕸️</span>
            <span className="tag">B2B · Institutions</span>
            <h2>Money Mule Detection</h2>
            <p>Map multi-account transaction networks and review evidence-backed alerts, with humans making every decision.</p>
            <span className="go">Open investigator portal →</span>
          </Link>
        </div>
        <p className="fineprint">Not a registered Account Aggregator, lender or adviser. All demo data is synthetic and labelled as such.</p>
      </div>
    </div>
  );
}

class ErrorBoundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="landing"><div className="card login stack">
        <h2>Something went wrong 😕</h2>
        <p className="muted">{String(this.state.err.message || this.state.err)}</p>
        <button onClick={() => { this.setState({ err: null }); location.reload(); }}>Reload</button>
      </div></div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ErrorBoundary>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/finlens/*" element={<FinLensApp />} />
        <Route path="/institution/*" element={<InstApp />} />
      </Routes>
    </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
);
