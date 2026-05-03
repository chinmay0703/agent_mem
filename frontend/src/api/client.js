// All API traffic flows through Vite's /api proxy in dev, and through the
// host's reverse proxy in prod. Set VITE_API_BASE to override.
const BASE = import.meta.env.VITE_API_BASE || "/api";

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

const FILE_BASE = BASE;

export function listFiles(userId) {
  return jsonFetch(`/files/${encodeURIComponent(userId)}`);
}

export async function uploadFile(userId, file, threadId = null) {
  const fd = new FormData();
  fd.append("file", file);
  if (threadId) fd.append("thread_id", threadId);
  const res = await fetch(
    `${FILE_BASE}/files/${encodeURIComponent(userId)}`,
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
