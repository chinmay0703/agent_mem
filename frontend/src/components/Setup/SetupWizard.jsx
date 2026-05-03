import React, { useEffect, useMemo, useState } from "react";
import {
  getSetupStatus,
  saveSetup,
  testNeo4j,
  testOpenAI,
  testPostgres,
} from "../../api/client.js";
import GraphBackdrop from "./GraphBackdrop.jsx";
import DemoLoop from "./DemoLoop.jsx";

// Step definitions — order matters; "review" must be last so the wizard
// gates the final save behind a successful test of every prior step.
const STEPS = [
  {
    id: "openai",
    label: "OpenAI",
    title: "OpenAI API Key",
    blurb: "Used for chat replies, memory extraction, and file embeddings.",
  },
  {
    id: "postgres",
    label: "Postgres",
    title: "Postgres Database",
    blurb: "Stores threads, messages, audit log, file metadata, and chunk index.",
  },
  {
    id: "neo4j",
    label: "Neo4j",
    title: "Neo4j Knowledge Graph",
    blurb: "Stores per-user facts as a typed entity graph with date-bounded edges.",
  },
  {
    id: "review",
    label: "Launch",
    title: "Review & Launch",
    blurb: "We'll persist your config, hot-swap connections, and drop you into the app.",
  },
];

const DEFAULTS = {
  openai: {
    api_key: "",
    model_name: "gpt-5.2",
    embedding_model: "text-embedding-3-small",
  },
  postgres: {
    host: "localhost",
    port: 5432,
    database: "chatbot_kg",
    user: "postgres",
    password: "",
    create_if_missing: true,
  },
  neo4j: {
    uri: "neo4j://127.0.0.1:7687",
    user: "neo4j",
    password: "",
    database: "neo4j",
  },
};

// `embedded` — when true, the wizard renders as an inline section inside
// the parent layout (LandingPage) so the site nav + footer remain
// visible. When false (legacy / standalone use) it takes over the
// whole viewport and brings its own background.
export default function SetupWizard({ onComplete, onBack, embedded = false }) {
  const [stepIdx, setStepIdx] = useState(0);
  const [values, setValues] = useState(DEFAULTS);
  const [tests, setTests] = useState({ openai: null, postgres: null, neo4j: null });
  const [busy, setBusy] = useState(false);
  const [launchErr, setLaunchErr] = useState(null);

  // Hydrate any previously-saved values so a re-visit shows them.
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const s = await getSetupStatus();
        if (cancel) return;
        const v = s.values || {};
        setValues((cur) => ({
          openai: { ...cur.openai, ...(v.openai || {}) },
          postgres: { ...cur.postgres, ...(v.postgres || {}) },
          neo4j: { ...cur.neo4j, ...(v.neo4j || {}) },
        }));
      } catch (_) {
        /* fresh install — keep defaults */
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  const step = STEPS[stepIdx];
  const canAdvance = useMemo(() => {
    if (step.id === "review")
      return tests.openai?.ok && tests.postgres?.ok && tests.neo4j?.ok;
    return tests[step.id]?.ok;
  }, [step, tests]);

  function patch(section, partial) {
    setValues((cur) => ({ ...cur, [section]: { ...cur[section], ...partial } }));
    setTests((cur) => ({ ...cur, [section]: null }));
  }

  async function runTest() {
    setBusy(true);
    try {
      let res;
      if (step.id === "openai") res = await testOpenAI(values.openai);
      else if (step.id === "postgres") res = await testPostgres(values.postgres);
      else if (step.id === "neo4j") res = await testNeo4j(values.neo4j);
      setTests((cur) => ({ ...cur, [step.id]: res }));
    } catch (e) {
      setTests((cur) => ({
        ...cur,
        [step.id]: { ok: false, error: e.message || "Request failed" },
      }));
    } finally {
      setBusy(false);
    }
  }

  async function launch() {
    setLaunchErr(null);
    setBusy(true);
    try {
      const res = await saveSetup({
        openai: values.openai,
        postgres: values.postgres,
        neo4j: values.neo4j,
      });
      if (!res.ok) {
        setLaunchErr("Save returned not-ok.");
        return;
      }
      if (res.schema_initialized === false) {
        setLaunchErr(`Saved, but schema init failed: ${res.schema_error}`);
        return;
      }
      onComplete?.();
    } catch (e) {
      setLaunchErr(e.message || "Save failed");
    } finally {
      setBusy(false);
    }
  }

  // The wizard body — same in either render mode.
  const body = (
    <div className={`setup-shell ${embedded ? "setup-shell-embedded" : ""}`}>
      {!embedded && (
        <header className="setup-header">
          <div className="setup-brand">
            <span className="setup-logo">
              <img
                src="/agent-mem-icon.png"
                alt="Agent Mem"
                className="setup-logo-img"
                draggable="false"
              />
              <span className="setup-logo-pulse" />
            </span>
            <div>
              <h1>Agent Mem</h1>
              <p>Memory-augmented AI agent · first-run setup</p>
            </div>
            {onBack && (
              <button
                type="button"
                className="setup-back"
                onClick={onBack}
                title="Back to home"
              >
                Home
              </button>
            )}
          </div>
        </header>
      )}

      {embedded && (
        <div className="setup-embedded-head">
          <div>
            <span className="setup-section-tag">Setup wizard</span>
            <h2>Connect your services. Three short steps.</h2>
            <p>
              Each connection is verified live before we save anything. You
              can come back and edit any of these later from inside the app.
              Use the navbar above to return home at any time.
            </p>
          </div>
        </div>
      )}

      <ol className="setup-steps">
        {STEPS.map((s, i) => {
          const state =
            i < stepIdx ? "done" : i === stepIdx ? "active" : "pending";
          const passed = tests[s.id]?.ok;
          return (
            <li
              key={s.id}
              className={`setup-step ${state} ${passed ? "passed" : ""}`}
              onClick={() => i <= stepIdx && setStepIdx(i)}
            >
              <span className="setup-step-num">{i + 1}</span>
              <span className="setup-step-label">{s.label}</span>
            </li>
          );
        })}
      </ol>

      <div className="setup-grid-wrap">
        <main className="setup-card">
          <div className="setup-card-head">
            <span className="setup-card-num">{String(stepIdx + 1).padStart(2, "0")}</span>
            <div>
              <h2>{step.title}</h2>
              <p className="setup-card-blurb">{step.blurb}</p>
            </div>
          </div>

          {step.id === "openai" && (
            <OpenAIForm values={values.openai} onChange={(p) => patch("openai", p)} />
          )}
          {step.id === "postgres" && (
            <PostgresForm
              values={values.postgres}
              onChange={(p) => patch("postgres", p)}
            />
          )}
          {step.id === "neo4j" && (
            <Neo4jForm values={values.neo4j} onChange={(p) => patch("neo4j", p)} />
          )}
          {step.id === "review" && (
            <ReviewPanel values={values} tests={tests} />
          )}

          {step.id !== "review" && <TestResult result={tests[step.id]} />}
          {step.id === "review" && launchErr && (
            <div className="setup-banner err">{launchErr}</div>
          )}

          <div className="setup-actions">
            <button
              className="setup-btn setup-btn-ghost"
              onClick={() => setStepIdx((i) => Math.max(0, i - 1))}
              disabled={stepIdx === 0 || busy}
            >
              Back
            </button>
            <span className="setup-actions-spacer" />
            {step.id !== "review" ? (
              <>
                <button
                  className="setup-btn setup-btn-secondary"
                  onClick={runTest}
                  disabled={busy}
                >
                  {busy ? (
                    <>
                      <span className="setup-spinner" /> Testing…
                    </>
                  ) : (
                    "Test connection"
                  )}
                </button>
                <button
                  className="setup-btn setup-btn-primary"
                  onClick={() =>
                    setStepIdx((i) => Math.min(STEPS.length - 1, i + 1))
                  }
                  disabled={!canAdvance || busy}
                  title={
                    canAdvance
                      ? "Continue"
                      : "Run a successful Test connection first"
                  }
                >
                  Continue
                </button>
              </>
            ) : (
              <button
                className="setup-btn setup-btn-primary setup-btn-launch"
                onClick={launch}
                disabled={!canAdvance || busy}
              >
                {busy ? (
                  <>
                    <span className="setup-spinner" /> Launching…
                  </>
                ) : (
                  "Launch Agent Mem"
                )}
              </button>
            )}
          </div>
        </main>

        <aside className="setup-side">
          <DemoLoop />
        </aside>
      </div>

      {!embedded && (
        <footer className="setup-foot">
          <span>
            <strong>Your credentials stay yours.</strong> Encrypted at rest,
            owner-only, and stored only for as long as this app needs them —
            we never see them, log them, or send them anywhere.
          </span>
        </footer>
      )}
    </div>
  );

  if (embedded) return body;
  return (
    <div className="setup-root">
      <GraphBackdrop />
      {body}
    </div>
  );
}

function Field({ label, hint, optional, children }) {
  return (
    <label className="setup-field">
      <span className="setup-field-label">
        {label}
        {optional && <span className="setup-field-optional"> · optional</span>}
      </span>
      {children}
      {hint && <span className="setup-field-hint">{hint}</span>}
    </label>
  );
}

function OpenAIForm({ values, onChange }) {
  return (
    <div className="setup-grid">
      <div className="setup-col-span-2">
        <Field
          label="API Key"
          hint="Get one at platform.openai.com/api-keys. Stored on disk only."
        >
          <input
            type="password"
            autoComplete="off"
            spellCheck="false"
            placeholder="sk-..."
            value={values.api_key || ""}
            onChange={(e) => onChange({ api_key: e.target.value })}
          />
        </Field>
      </div>
      <Field label="Chat model" hint="Reasoning model recommended (gpt-5.2)" optional>
        <input
          value={values.model_name || ""}
          onChange={(e) => onChange({ model_name: e.target.value })}
          placeholder="gpt-5.2"
        />
      </Field>
      <Field label="Embedding model" hint="Used for RAG over uploaded files" optional>
        <input
          value={values.embedding_model || ""}
          onChange={(e) => onChange({ embedding_model: e.target.value })}
          placeholder="text-embedding-3-small"
        />
      </Field>
    </div>
  );
}

function PostgresForm({ values, onChange }) {
  return (
    <div className="setup-grid">
      <Field label="Host">
        <input value={values.host} onChange={(e) => onChange({ host: e.target.value })} />
      </Field>
      <Field label="Port">
        <input
          type="number"
          value={values.port}
          onChange={(e) => onChange({ port: Number(e.target.value) || 0 })}
        />
      </Field>
      <Field label="Database" hint="Created automatically if missing.">
        <input
          value={values.database}
          onChange={(e) => onChange({ database: e.target.value })}
        />
      </Field>
      <Field label="User">
        <input value={values.user} onChange={(e) => onChange({ user: e.target.value })} />
      </Field>
      <div className="setup-col-span-2">
        <Field label="Password">
          <input
            type="password"
            autoComplete="off"
            value={values.password}
            onChange={(e) => onChange({ password: e.target.value })}
          />
        </Field>
      </div>
      <label className="setup-check">
        <input
          type="checkbox"
          checked={!!values.create_if_missing}
          onChange={(e) => onChange({ create_if_missing: e.target.checked })}
        />
        <span>Create the database if it doesn't exist</span>
      </label>
    </div>
  );
}

function Neo4jForm({ values, onChange }) {
  return (
    <div className="setup-grid">
      <div className="setup-col-span-2">
        <Field
          label="URI"
          hint="bolt:// or neo4j:// — for Aura Cloud use neo4j+s://… (TLS)"
        >
          <input value={values.uri} onChange={(e) => onChange({ uri: e.target.value })} />
        </Field>
      </div>
      <Field label="User">
        <input value={values.user} onChange={(e) => onChange({ user: e.target.value })} />
      </Field>
      <Field label="Password">
        <input
          type="password"
          autoComplete="off"
          value={values.password}
          onChange={(e) => onChange({ password: e.target.value })}
        />
      </Field>
      <div className="setup-col-span-2">
        <Field
          label="Database"
          hint="Community Edition only has the default 'neo4j' database."
        >
          <input
            value={values.database}
            onChange={(e) => onChange({ database: e.target.value })}
          />
        </Field>
      </div>
    </div>
  );
}

function TestResult({ result }) {
  if (!result) return null;
  const cls = result.ok ? "setup-banner ok" : "setup-banner err";
  return (
    <div className={cls}>
      <span>
        {result.ok
          ? result.message || "Connection successful."
          : result.error || "Failed."}
      </span>
    </div>
  );
}

function ReviewPanel({ values, tests }) {
  const rows = [
    {
      label: "OpenAI",
      passed: tests.openai?.ok,
      lines: [
        `Key: ${maskInline(values.openai.api_key)}`,
        `Model: ${values.openai.model_name}`,
      ],
    },
    {
      label: "Postgres",
      passed: tests.postgres?.ok,
      lines: [
        `${values.postgres.user}@${values.postgres.host}:${values.postgres.port}`,
        `Database: ${values.postgres.database}`,
      ],
    },
    {
      label: "Neo4j",
      passed: tests.neo4j?.ok,
      lines: [
        `${values.neo4j.user}@${values.neo4j.uri}`,
        `Database: ${values.neo4j.database}`,
      ],
    },
  ];
  const allOk = rows.every((r) => r.passed);
  return (
    <div className="setup-review">
      {rows.map((r) => (
        <div key={r.label} className={`setup-review-row ${r.passed ? "ok" : "pending"}`}>
          <div className="setup-review-head">
            <strong>{r.label}</strong>
            {r.passed ? (
              <span className="setup-review-tag">verified</span>
            ) : (
              <span className="setup-review-tag pending">not tested</span>
            )}
          </div>
          {r.lines.map((l, i) => (
            <div key={i} className="setup-review-line">{l}</div>
          ))}
        </div>
      ))}
      {!allOk && (
        <div className="setup-banner warn">
          One or more sections aren't validated yet — step back and run “Test
          connection” for any pending row.
        </div>
      )}
    </div>
  );
}

function maskInline(s) {
  if (!s) return "(empty)";
  if (s.length <= 8) return "•".repeat(s.length);
  return s.slice(0, 3) + "•".repeat(Math.min(s.length - 6, 12)) + s.slice(-3);
}
