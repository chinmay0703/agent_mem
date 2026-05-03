import React, { createContext, useCallback, useContext, useState } from "react";

const ToastCtx = createContext(null);

let _id = 0;

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);

  const dismiss = useCallback((id) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = useCallback(
    (msg, kind = "info", ttl = 3500) => {
      const id = ++_id;
      setItems((xs) => [...xs, { id, msg, kind }]);
      if (ttl > 0) setTimeout(() => dismiss(id), ttl);
      return id;
    },
    [dismiss],
  );

  const api = {
    info: (m, ttl) => push(m, "info", ttl),
    success: (m, ttl) => push(m, "success", ttl),
    error: (m, ttl) => push(m, "error", ttl),
    warn: (m, ttl) => push(m, "warn", ttl),
    dismiss,
  };

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toast-stack">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => dismiss(t.id)}>
            {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastCtx);
  if (!ctx) {
    // Standalone fallback so a missing provider doesn't crash callers.
    return {
      info: (m) => console.log("[toast]", m),
      success: (m) => console.log("[toast]", m),
      error: (m) => console.error("[toast]", m),
      warn: (m) => console.warn("[toast]", m),
      dismiss: () => {},
    };
  }
  return ctx;
}
