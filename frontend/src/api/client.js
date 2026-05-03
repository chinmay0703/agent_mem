// All API traffic flows through Vite's /api proxy in dev, and through the
// host's reverse proxy in prod. The user can also point the deployed
// frontend at a backend running on their own machine — for that case we
// read a runtime override from localStorage (set by the wizard's
// "Backend URL" step). Precedence: localStorage > VITE_API_BASE > /api.
const LS_KEY = "chatmem.apiBase";

function readBase() {
  try {
    const v = localStorage.getItem(LS_KEY);
    if (v && typeof v === "string") return v.replace(/\/+$/, "");
  } catch (_) {}
  return import.meta.env.VITE_API_BASE || "/api";
}

let BASE = readBase();

export function getApiBase() {
  return BASE;
}

export function setApiBase(url) {
  // Normalize: trim, strip trailing slash, allow either
  // `http://host:port` or `http://host:port/api`. Same backend either way.
  const cleaned = (url || "").trim().replace(/\/+$/, "");
  BASE = cleaned || "/api";
  try {
    if (cleaned) localStorage.setItem(LS_KEY, cleaned);
    else localStorage.removeItem(LS_KEY);
  } catch (_) {}
}

export async function pingBackend(url) {
  // Health probe used by the wizard's "Backend URL" step. The FastAPI
  // app exposes /health AND /api/health, so we accept either form of
  // URL the user pasted (with or without /api suffix).
  const cleaned = (url || "").trim().replace(/\/+$/, "");
  if (!cleaned) throw new Error("Enter a URL.");
  // Try /api/health first (matches the canonical mount), then /health.
  const candidates = cleaned.endsWith("/api")
    ? [`${cleaned}/health`, `${cleaned.slice(0, -4)}/health`]
    : [`${cleaned}/api/health`, `${cleaned}/health`];
  let lastErr = null;
  for (const u of candidates) {
    try {
      const res = await fetch(u, { method: "GET" });
      if (res.ok) {
        const body = await res.json().catch(() => ({}));
        return { ok: true, url: cleaned, status: body.status || "ok" };
      }
      lastErr = `${res.status} ${res.statusText}`;
    } catch (e) {
      lastErr = e.message || "fetch failed";
    }
  }
  throw new Error(`Backend unreachable at ${cleaned} (${lastErr}).`);
}

// Surface-level cap so we can show friendlier messages for big inputs.
export const MAX_MESSAGE_CHARS = 4000;

async function jsonFetch(path, init) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (e) {
    if (e?.name === "AbortError") throw e; // bubble up untouched
    throw new Error("Network error — is the backend reachable?");
  }
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    body = null;
  }
  if (!res.ok) {
    // Backend returns { error: { status, detail, ... } } — fall back to status.
    const detail = body?.error?.detail || body?.detail || res.statusText;
    if (res.status === 429) throw new Error("Rate limit hit. Slow down a bit.");
    if (res.status === 401) throw new Error("Unauthorized.");
    if (res.status === 413) throw new Error("Payload too large.");
    throw new Error(`${res.status}: ${detail}`);
  }
  return body;
}

export function sendMessage(
  { userId, threadId, message, regenerate = false },
  init = {},
) {
  return jsonFetch("/chat", {
    method: "POST",
    body: JSON.stringify({
      user_id: userId,
      thread_id: threadId,
      message,
      regenerate,
    }),
    ...init,
  });
}

export function fetchGraph(userId) {
  return jsonFetch(`/memory/graph/${encodeURIComponent(userId)}`);
}

export function fetchSemantic(userId) {
  return jsonFetch(`/memory/semantic/${encodeURIComponent(userId)}`);
}

export function listThreads(userId) {
  return jsonFetch(`/threads/${encodeURIComponent(userId)}`);
}

export function getThread(userId, threadId) {
  return jsonFetch(
    `/threads/${encodeURIComponent(userId)}/${encodeURIComponent(threadId)}`,
  );
}

export function deleteThread(userId, threadId) {
  return jsonFetch(
    `/threads/${encodeURIComponent(userId)}/${encodeURIComponent(threadId)}`,
    { method: "DELETE" },
  );
}

export function fetchGraphStats(userId) {
  return jsonFetch(`/memory/stats/${encodeURIComponent(userId)}`);
}

export function deleteTriple(userId, triple) {
  return jsonFetch(`/memory/triple/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    body: JSON.stringify(triple),
  });
}

export function deleteUser(userId) {
  return jsonFetch(`/users/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}

export function listFiles(userId) {
  return jsonFetch(`/files/${encodeURIComponent(userId)}`);
}

export async function uploadFile(userId, file, threadId = null) {
  const fd = new FormData();
  fd.append("file", file);
  if (threadId) fd.append("thread_id", threadId);
  // Read BASE at call time so a runtime setApiBase() takes effect.
  const res = await fetch(
    `${BASE}/files/${encodeURIComponent(userId)}`,
    { method: "POST", body: fd },
  );
  let body = null;
  try {
    body = await res.json();
  } catch (_) {}
  if (!res.ok) {
    const detail = body?.error?.detail || body?.detail || res.statusText;
    throw new Error(`${res.status}: ${detail}`);
  }
  return body;
}

export function deleteFile(userId, fileId) {
  return jsonFetch(
    `/files/${encodeURIComponent(userId)}/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
}

// ── First-run setup wizard ──────────────────────────────────────────────
export function getSetupStatus() {
  return jsonFetch("/setup/status");
}

export function testOpenAI(body) {
  return jsonFetch("/setup/test/openai", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function testPostgres(body) {
  return jsonFetch("/setup/test/postgres", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function testNeo4j(body) {
  return jsonFetch("/setup/test/neo4j", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function saveSetup(body) {
  return jsonFetch("/setup/save", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function resetSetup() {
  return jsonFetch("/setup/config", { method: "DELETE" });
}
