interface Props {
  onStart: () => void;
}

const steps = [
  {
    number: "01",
    title: "Bring your CSVs",
    description: "Upload related files together. The analyst profiles tables and finds how they connect.",
    icon: "↥",
  },
  {
    number: "02",
    title: "Understand your data",
    description: "Review relationships, missing values, duplicates, and other data-quality signals.",
    icon: "⌕",
  },
  {
    number: "03",
    title: "Ask in plain English",
    description: "Explore answers as charts or tables, inspect the SQL, and download results.",
    icon: "↗",
  },
];

export default function LandingView({ onStart }: Props) {
  return (
    <main className="landing">
      <header className="landing-nav">
        <a className="landing-brand" href="/" aria-label="CSV Chat Analyst home">
          <span className="landing-brand-mark" aria-hidden>
            <svg viewBox="0 0 32 32" fill="none">
              <rect x="4" y="5" width="24" height="22" rx="6" />
              <path d="M4 12h24M12 12v15m8-15v15M4 19h24" />
            </svg>
          </span>
          <span>csv<span className="landing-brand-light">chat</span></span>
        </a>
        <nav aria-label="Main navigation">
          <a className="landing-nav-link" href="#how-it-works">How it works</a>
          <button className="landing-nav-cta" onClick={onStart}>Open the analyst <span aria-hidden>↗</span></button>
        </nav>
      </header>

      <section className="landing-hero">
        <div className="landing-copy">
          <div className="landing-eyebrow"><span /> A clearer view of your data</div>
          <h1>Your CSVs have<br />a lot to <span>say.</span></h1>
          <p className="landing-subtitle">
            Bring your files together, then ask questions in plain English. Get grounded answers, useful charts,
            and the SQL behind every result.
          </p>
          <div className="landing-actions">
            <button className="landing-primary" onClick={onStart}>Start exploring <span aria-hidden>→</span></button>
            <span className="landing-limit">Up to 10 CSV files · 10 MB per file</span>
          </div>
          <div className="landing-proof">
            <span className="landing-proof-icon" aria-hidden>✓</span>
            No setup. Just bring your data.
          </div>
        </div>

        <div className="landing-preview" aria-label="How CSV Chat Analyst works">
          <div className="preview-topbar">
            <span className="preview-dot" /><span className="preview-dot" /><span className="preview-dot" />
            <span className="preview-label">YOUR DATA, AT A GLANCE</span>
          </div>
          <div className="preview-files">
            <div className="preview-file"><span className="preview-file-icon">▦</span><span><b>orders.csv</b><small>1,284 rows · 8 columns</small></span><span className="preview-check">✓</span></div>
            <div className="preview-file"><span className="preview-file-icon">▦</span><span><b>customers.csv</b><small>326 rows · 6 columns</small></span><span className="preview-check">✓</span></div>
            <div className="preview-file"><span className="preview-file-icon">▦</span><span><b>products.csv</b><small>84 rows · 5 columns</small></span><span className="preview-check">✓</span></div>
          </div>
          <div className="preview-connection"><span /> Found a connection <b>orders ↔ customers</b></div>
          <div className="preview-question"><span className="preview-sparkle">✳</span><span>Which customers ordered the most?</span><span className="preview-send">↑</span></div>
          <div className="preview-answer">
            <span className="preview-answer-tag">ANSWER</span>
            <div className="preview-bars" aria-hidden>
              <i style={{ height: "38%" }} /><i style={{ height: "68%" }} /><i style={{ height: "52%" }} /><i style={{ height: "88%" }} /><i style={{ height: "61%" }} /><i style={{ height: "100%" }} />
            </div>
            <div className="preview-answer-footer"><span>Top customers by orders</span><span>SQL included ↗</span></div>
          </div>
          <div className="preview-glow" aria-hidden />
        </div>
      </section>

      <section className="landing-steps" id="how-it-works">
        <div className="landing-section-heading">
          <span>FROM FILES TO FINDINGS</span>
          <h2>Less wrangling. More wondering.</h2>
        </div>
        <div className="landing-step-grid">
          {steps.map((step) => (
            <article className="landing-step" key={step.number}>
              <div className="landing-step-top"><span className="landing-step-icon" aria-hidden>{step.icon}</span><span>{step.number}</span></div>
              <h3>{step.title}</h3>
              <p>{step.description}</p>
            </article>
          ))}
        </div>
      </section>

      <aside className="landing-demo-note">
        <span aria-hidden>ⓘ</span>
        Public demo: please don’t upload sensitive data. Uploaded files and sessions may not persist.
      </aside>

      <footer className="landing-footer">
        <a className="landing-brand landing-footer-brand" href="/" aria-label="CSV Chat Analyst home">
          <span className="landing-brand-mark" aria-hidden>
            <svg viewBox="0 0 32 32" fill="none">
              <rect x="4" y="5" width="24" height="22" rx="6" />
              <path d="M4 12h24M12 12v15m8-15v15M4 19h24" />
            </svg>
          </span>
          <span>csv<span className="landing-brand-light">chat</span></span>
        </a>
        <span>Made by <a href="https://debuggedself.vercel.app/" target="_blank" rel="noreferrer">Harsh Gupta <span aria-hidden>↗</span></a></span>
      </footer>
    </main>
  );
}
