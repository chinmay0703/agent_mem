import React, { useEffect, useMemo, useState } from "react";
import {
  getApiBase,
  getSetupStatus,
  pingBackend,
  saveSetup,
  setApiBase,
  testNeo4j,
  testOpenAI,
  testPostgres,
} from "../../api/client.js";
import GraphBackdrop from "./GraphBackdrop.jsx";
import DemoLoop from "./DemoLoop.jsx";

// Step definitions — order matters; "review" must be last so the wizard
// gates the final save behind a successful test of every prior step.
// "backend" runs first because everything below it (OpenAI/PG/Neo4j
// tests, save) goes through the user's chosen backend URL.
const STEPS = [
  {
    id: "backend",
    label: "Backend",
    title: "Where is your backend?",
    blurb:
      "The Python backend talks to Postgres / Neo4j on your machine. Run it locally, then paste its URL here. The deployed site never touches your databases directly.",
  },
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
  backend: {
    // mode: "hosted" -> use the API that ships with this deploy
    //                  (right path for cloud DBs: Neon, Aura, Supabase,
    //                  Vercel Postgres — anything reachable from the
    //                  public internet).
    // mode: "local"  -> run our Python backend on the user's machine,
    //                   for localhost Postgres / Neo4j.
    mode: (() => {
      const b = getApiBase();
      // If a previous session pointed us at a localhost backend, start
      // the wizard in local mode so the URL stays editable.
      if (b && b !== "/api" && /localhost|127\.0\.0\.1/i.test(b)) return "local";
      return "hosted";
    })(),
    url: (() => {
      const b = getApiBase();
      return b && b !== "/api" ? b.replace(/\/api$/, "") : "http://localhost:8000";
    })(),
  },
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
  const [tests, setTests] = useState({
    backend: null,
    openai: null,
    postgres: null,
    neo4j: null,
  });
  const [busy, setBusy] = useState(false);
  const [launchErr, setLaunchErr] = useState(null);

  // Hydrate any previously-saved values so a re-visit shows them.
  // We only fetch /setup/status if the user has already pointed us at a
  // working backend — otherwise the request will fail and dump us into
  // the catch block (which is fine, we just keep defaults).
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const s = await getSetupStatus();
        if (cancel) return;
        const v = s.values || {};
        setValues((cur) => ({
          ...cur,
          openai: { ...cur.openai, ...(v.openai || {}) },
          postgres: { ...cur.postgres, ...(v.postgres || {}) },
          neo4j: { ...cur.neo4j, ...(v.neo4j || {}) },
        }));
        // If status loaded successfully, the backend URL is already
        // good — pre-pass that step so the user lands on OpenAI.
        setTests((cur) => ({ ...cur, backend: { ok: true, message: "Backend reachable." } }));
      } catch (_) {
        /* backend not yet reachable — wizard will gate at step 1 */
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  const step = STEPS[stepIdx];
  const canAdvance = useMemo(() => {
    if (step.id === "review")
      return (
        tests.backend?.ok &&
        tests.openai?.ok &&
        tests.postgres?.ok &&
        tests.neo4j?.ok
      );
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
      if (step.id === "backend") {
        if (values.backend.mode === "hosted") {
          // Use the API that ships with this deploy. Clear any stale
          // localhost override so subsequent calls hit /api.
          setApiBase("");
          const r = await pingBackend(window.location.origin);
          res = {
            ok: true,
            message: `Hosted backend reachable at ${r.url}/api`,
          };
        } else {
          const r = await pingBackend(values.backend.url);
          // Persist so every subsequent API call (openai/pg/neo4j tests
          // and the final save) goes to this backend.
          setApiBase(r.url);
          res = { ok: true, message: `Backend reachable at ${r.url}` };
        }
      } else if (step.id === "openai") res = await testOpenAI(values.openai);
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

          {step.id === "backend" && (
            <BackendForm
              values={values.backend}
              onChange={(p) => patch("backend", p)}
            />
          )}
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

function BackendForm({ values, onChange }) {
  const isLocal = values.mode === "local";
  return (
    <div className="setup-grid">
      <div className="setup-col-span-2">
        <div className="backend-modes">
          <button
            type="button"
            className={`backend-mode ${!isLocal ? "active" : ""}`}
            onClick={() => onChange({ mode: "hosted" })}
            aria-pressed={!isLocal}
          >
            <span className="backend-mode-dot" aria-hidden />
            <span className="backend-mode-glyph" aria-hidden>☁</span>
            <span className="backend-mode-title">Cloud databases</span>
            <span className="backend-mode-pill">Recommended</span>
            <span className="backend-mode-desc">
              Use the API that ships with this site. Best for Neon,
              Supabase, Vercel Postgres, Neo4j Aura — any DB reachable
              from the public internet.
            </span>
            <span className="backend-mode-fine">No install. Paste connection strings.</span>
          </button>
          <button
            type="button"
            className={`backend-mode ${isLocal ? "active" : ""}`}
            onClick={() => onChange({ mode: "local" })}
            aria-pressed={isLocal}
          >
            <span className="backend-mode-dot" aria-hidden />
            <span className="backend-mode-glyph" aria-hidden>⌂</span>
            <span className="backend-mode-title">Localhost databases</span>
            <span className="backend-mode-desc">
              For Postgres / Neo4j running on your laptop. Run our Python
              backend locally; this site talks to it from your browser.
            </span>
            <span className="backend-mode-fine">DB credentials never leave your machine.</span>
          </button>
        </div>
      </div>

      {isLocal && (
        <>
          <div className="setup-col-span-2">
            <Field
              label="Backend URL"
              hint="Default for a local docker-compose run is http://localhost:8000. Browsers allow https→http for localhost, so this works even from the deployed site."
            >
              <input
                type="url"
                spellCheck="false"
                placeholder="http://localhost:8000"
                value={values.url || ""}
                onChange={(e) => onChange({ url: e.target.value })}
              />
            </Field>
          </div>
          <div className="setup-col-span-2">
            <div className="backend-help">
              <div className="backend-help-title">Don't have it running?</div>
              <p className="backend-help-body">
                From the project root, run one of:
              </p>
              <pre className="backend-help-cmd">docker compose up</pre>
              <pre className="backend-help-cmd">cd backend &amp;&amp; uvicorn app.main:app --port 8000</pre>
              <p className="backend-help-foot">
                The backend stays on your machine — your DB credentials never
                leave it.
              </p>
            </div>
          </div>
        </>
      )}

      {!isLocal && (
        <div className="setup-col-span-2">
          <div className="backend-help">
            <div className="backend-help-title">Heads-up for hosted DBs</div>
            <p className="backend-help-body">
              In the next steps, paste your <strong>public</strong> connection
              strings:
            </p>
            <ul className="backend-help-list">
              <li>
                <strong>Postgres:</strong> Neon / Supabase /
                Vercel Postgres host (use the SSL endpoint, e.g.
                <code>ep-xxx.neon.tech</code>).
              </li>
              <li>
                <strong>Neo4j:</strong> Aura URI starting with
                <code>neo4j+s://</code>.
              </li>
            </ul>
            <p className="backend-help-foot">
              Local <code>localhost</code> credentials won't reach the hosted
              backend — switch to the other mode above if you need that.
            </p>
          </div>
        </div>
      )}
    </div>
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
      label: "Backend",
      passed: tests.backend?.ok,
      lines:
        values.backend?.mode === "local"
          ? [`Mode: local`, `URL: ${values.backend?.url || "(not set)"}`]
          : [`Mode: hosted`, `URL: ${window.location.origin}/api`],
    },
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
