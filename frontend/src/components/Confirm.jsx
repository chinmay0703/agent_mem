import React, { createContext, useCallback, useContext, useState } from "react";
import Modal from "./Modal.jsx";

const ConfirmCtx = createContext(null);

/**
 * Promise-based in-app confirm dialog.
 *
 *   const confirm = useConfirm();
 *   if (await confirm({ title: "Delete?", body: "...", danger: true })) {
 *     // user clicked OK
 *   }
 *
 * Replaces window.confirm() so the look is consistent with the rest of the
 * app and keyboard/escape behavior is reliable.
 */
export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null);

  const confirm = useCallback((opts) => {
    return new Promise((resolve) => {
      setState({
        title: opts.title || "Are you sure?",
        body: opts.body || "",
        confirmLabel: opts.confirmLabel || "Confirm",
        cancelLabel: opts.cancelLabel || "Cancel",
        danger: !!opts.danger,
        resolve,
      });
    });
  }, []);

  const close = useCallback(
    (result) => {
      setState((s) => {
        if (s) s.resolve(result);
        return null;
      });
    },
    [],
  );

  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      <Modal
        open={!!state}
        title={state?.title || ""}
        onClose={() => close(false)}
        footer={
          <>
            <button className="btn" onClick={() => close(false)}>
              {state?.cancelLabel || "Cancel"}
            </button>
            <button
              className={state?.danger ? "btn btn-danger" : "btn btn-primary"}
              onClick={() => close(true)}
              autoFocus
            >
              {state?.confirmLabel || "Confirm"}
            </button>
          </>
        }
      >
        {typeof state?.body === "string" ? (
          <p style={{ margin: 0 }}>{state.body}</p>
        ) : (
          state?.body
        )}
      </Modal>
    </ConfirmCtx.Provider>
  );
}

export function useConfirm() {
  const ctx = useContext(ConfirmCtx);
  if (!ctx) {
    // Standalone fallback so a missing provider doesn't crash the app —
    // falls back to the native dialog rather than swallowing the prompt.
    return async ({ title = "Confirm", body = "" } = {}) =>
      window.confirm(`${title}\n\n${body}`);
  }
  return ctx;
}
