import React, { useState } from "react";
import GraphBackdrop from "./GraphBackdrop.jsx";
import DemoLoop from "./DemoLoop.jsx";
import SetupWizard from "./SetupWizard.jsx";

// Public marketing surface that fronts the setup wizard. The wizard is
// rendered as an inline section inside this layout (instead of taking
// over the whole viewport), so the nav + footer remain visible the
// entire time.

const FEATURES = [
  {
    title: "Living memory",
    body:
      "Every conversation builds your AI's understanding of you. Facts, plans, and relationships persist across threads — and across days.",
  },
  {
    title: "Document intelligence",
    body:
      "Upload PDFs, spreadsheets, and docs. Ask questions in natural language. Get answers with inline citations to the exact source.",
  },
  {
    title: "Reasoning agent",
    body:
      "Picks the right tool for the job. Plans before answering. Writes and runs analysis when numbers are involved.",
  },
  {
    title: "Yours alone",
    body:
      "Runs on your machine, on your credentials. Your data never leaves it. No telemetry, no analytics, no vendor lock-in.",
  },
];

const FLOW = [
  {
    step: "01",
    title: "Conversation",
    body: "Chat naturally. Upload anything you want the AI to know about.",
  },
  {
    step: "02",
    title: "Memory",
    body: "Every fact, relationship, and date is captured into a living knowledge graph.",
  },
  {
    step: "03",
    title: "Reasoning",
    body: "The agent plans, retrieves, runs tools, and stitches the answer together.",
  },
  {
    step: "04",
    title: "Response",
    body: "Cited, grounded, and saved. Future questions build on what's known.",
  },
];

const STATS = [
  { value: "0", label: "Bytes sent to us" },
  { value: "60s", label: "Setup time" },
  { value: "∞", label: "Memory horizon" },
];

export default function LandingPage({ onComplete }) {
  const [showSetup, setShowSetup] = useState(false);

  const start = () => {
    setShowSetup(true);
    requestAnimationFrame(() => {
      const el = document.getElementById("start");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };
  const close = () => setShowSetup(false);

  return (
    <div className="lp-root">
      <GraphBackdrop />

      <Nav onGetStarted={start} onClose={close} showingSetup={showSetup} />

      <main>
        {showSetup ? (
          <SetupWizard
            onComplete={onComplete}
            onBack={close}
            embedded
          />
        ) : (
          <>
            <Hero onGetStarted={start} />
            <Stats />
            <About />
            <DemoSection />
            <Architecture />
            <CtaBanner onGetStarted={start} />
          </>
        )}
      </main>

      <SiteFooter onGetStarted={start} onClose={close} />
    </div>
  );
}

function Nav({ onGetStarted, onClose, showingSetup }) {
  // Anchor-link clicks must (a) leave the wizard view if it's open and
  // (b) scroll to the requested section on the next frame, after the
  // landing page has re-rendered. Without (a), clicks while the wizard
  // is open do nothing visible.
  const goSection = (id) => (e) => {
    e.preventDefault();
    if (showingSetup) onClose?.();
    requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      } else {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
  };

  return (
    <header className="lp-nav">
      <a className="lp-brand" href="#top" onClick={goSection("top")}>
        <span className="lp-logo">
          <img
            src="/agent-mem-icon.png"
            alt="Agent Mem"
            className="lp-logo-img"
            draggable="false"
          />
          <span className="lp-logo-pulse" />
        </span>
        <span className="lp-brand-text">Agent Mem</span>
      </a>
      <nav className="lp-nav-links">
        <a href="#about" onClick={goSection("about")}>About</a>
        <a href="#demo" onClick={goSection("demo")}>Demo</a>
        <a href="#flow" onClick={goSection("flow")}>How it works</a>
        {showingSetup ? (
          <a
            href="#top"
            className="lp-nav-cta lp-nav-cta-back"
            onClick={(e) => {
              e.preventDefault();
              onClose?.();
              requestAnimationFrame(() =>
                window.scrollTo({ top: 0, behavior: "smooth" }),
              );
            }}
          >
            Back to home
          </a>
        ) : (
          <a
            href="#start"
            className="lp-nav-cta"
            onClick={(e) => {
              e.preventDefault();
              onGetStarted();
            }}
          >
            Get started
          </a>
        )}
      </nav>
    </header>
  );
}

function Hero({ onGetStarted }) {
  return (
    <section id="top" className="lp-hero">
      <div className="lp-hero-text">
        <span className="lp-eyebrow">Memory-augmented AI · v1</span>
        <h1 className="lp-hero-title">
          AI that <span className="lp-grad">actually remembers</span>.
        </h1>
        <p className="lp-hero-sub">
          Drop in your documents. Talk to it. It builds a knowledge graph as
          you chat — facts, relationships, plans, all retrievable later. No
          fine-tuning. No vendor lock-in. Yours.
        </p>
        <div className="lp-hero-ctas">
          <button className="lp-btn-primary" onClick={onGetStarted}>
            Get started — free
          </button>
          <a className="lp-btn-secondary" href="#demo">
            Watch the demo
          </a>
        </div>
        <p className="lp-hero-trust">
          Self-hosted · Your credentials · Your data · Your machine
        </p>
      </div>
      <div className="lp-hero-art" aria-hidden>
        <img
          src="/agent-mem-icon.png"
          alt=""
          className="lp-hero-orb"
          draggable="false"
        />
      </div>
    </section>
  );
}

function Stats() {
  return (
    <section className="lp-stats">
      {STATS.map((s) => (
        <div key={s.label} className="lp-stat">
          <span className="lp-stat-value">{s.value}</span>
          <span className="lp-stat-label">{s.label}</span>
        </div>
      ))}
    </section>
  );
}

function About() {
  return (
    <section id="about" className="lp-section">
      <header className="lp-section-head">
        <span className="lp-section-tag">What it does</span>
        <h2>An assistant that learns you, not just your last message.</h2>
        <p>
          Most chatbots forget the conversation the moment you close the tab.
          Agent Mem captures what matters into a structured graph — names,
          jobs, plans, preferences, dates — and brings it back, on its own,
          when it's useful.
        </p>
      </header>
      <div className="lp-features">
        {FEATURES.map((f, i) => (
          <div key={f.title} className={`lp-feature lp-feature-${i + 1}`}>
            <span className="lp-feature-bar" aria-hidden />
            <h3>{f.title}</h3>
            <p>{f.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function DemoSection() {
  return (
    <section id="demo" className="lp-section lp-demo-section">
      <header className="lp-section-head">
        <span className="lp-section-tag">See it work</span>
        <h2>Watch the memory graph grow as the conversation happens.</h2>
        <p>
          This is a recording of three real turns from the live agent —
          notice how each fact is anchored, typed, and linked into the graph
          on the right. No screenshots, no marketing fluff.
        </p>
      </header>
      <div className="lp-demo-stage">
        <DemoLoop />
      </div>
    </section>
  );
}

function Architecture() {
  return (
    <section id="flow" className="lp-section">
      <header className="lp-section-head">
        <span className="lp-section-tag">How it works</span>
        <h2>Four steps, every turn.</h2>
        <p>
          A small, focused pipeline: take what you said, learn from it,
          reason over what's known, send back something cited and useful.
        </p>
      </header>
      <ol className="lp-flow">
        {FLOW.map((step, i) => (
          <li key={step.step} className="lp-flow-step">
            <div className="lp-flow-card">
              <span className="lp-flow-num">{step.step}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </div>
            {i < FLOW.length - 1 && (
              <span className="lp-flow-arrow" aria-hidden />
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function CtaBanner({ onGetStarted }) {
  return (
    <section id="start" className="lp-cta">
      <h2>Stop re-explaining yourself to your AI.</h2>
      <p>Plug in your keys. Be chatting in under a minute.</p>
      <button className="lp-btn-primary lp-btn-large" onClick={onGetStarted}>
        Get started — free
      </button>
      <span className="lp-cta-fine">
        Encrypted at rest. Owner-only. We never see your keys.
      </span>
    </section>
  );
}

function SiteFooter({ onGetStarted, onClose }) {
  const goSection = (id) => (e) => {
    e.preventDefault();
    onClose?.();
    requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      else window.scrollTo({ top: 0, behavior: "smooth" });
    });
  };

  return (
    <footer className="lp-footer">
      <div className="lp-footer-cols">
        <div className="lp-footer-col lp-footer-brand-col">
          <a
            className="lp-footer-brand-block"
            href="#top"
            onClick={goSection("top")}
          >
            <span className="lp-logo">
              <span className="lp-logo-orb" />
              <span className="lp-logo-pulse" />
            </span>
            <span className="lp-footer-brand">Agent Mem</span>
          </a>
          <p>
            Memory-augmented AI for everyone. Self-hosted, private, yours.
          </p>
        </div>
        <div className="lp-footer-col">
          <h4>Product</h4>
          <ul>
            <li><a href="#about" onClick={goSection("about")}>About</a></li>
            <li><a href="#demo" onClick={goSection("demo")}>Live demo</a></li>
            <li><a href="#flow" onClick={goSection("flow")}>How it works</a></li>
          </ul>
        </div>
        <div className="lp-footer-col">
          <h4>Get started</h4>
          <ul>
            <li>
              <a
                href="#start"
                onClick={(e) => {
                  e.preventDefault();
                  onGetStarted?.();
                }}
              >
                Set up now
              </a>
            </li>
            <li><a href="#top" onClick={goSection("top")}>Back to top</a></li>
          </ul>
        </div>
        <div className="lp-footer-col">
          <h4>Trust</h4>
          <ul>
            <li>Self-hosted, not a SaaS</li>
            <li>Owner-only credentials</li>
            <li>No telemetry, ever</li>
          </ul>
        </div>
      </div>
      <div className="lp-footer-bar">
        <span>© {new Date().getFullYear()} Agent Mem · All rights reserved</span>
        <span>Built for people who don't want to repeat themselves.</span>
      </div>
    </footer>
  );
}
