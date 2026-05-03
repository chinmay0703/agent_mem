import React, { useRef } from "react";

/**
 * Vertical splitter between two columns. On pointer-drag we report the
 * horizontal delta to the parent so it can resize panel widths and
 * persist the new layout.
 */
export default function Splitter({ onDelta, ariaLabel = "Resize panel" }) {
  const startX = useRef(null);

  function handlePointerDown(e) {
    e.preventDefault();
    startX.current = e.clientX;
    document.body.classList.add("resizing-h");

    function onMove(ev) {
      const dx = ev.clientX - startX.current;
      if (dx === 0) return;
      onDelta(dx);
      startX.current = ev.clientX;
    }
    function onUp() {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.body.classList.remove("resizing-h");
    }
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      tabIndex={0}
      className="splitter"
      onPointerDown={handlePointerDown}
    >
      <span className="splitter-grip" />
    </div>
  );
}
