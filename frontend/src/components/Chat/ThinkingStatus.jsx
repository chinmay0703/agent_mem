import React, { useEffect, useState } from "react";

// Project-flavored "what the agent is doing right now" lines. Cycle while a
// turn is pending so the user feels the pipeline working instead of staring
// at three bouncing dots. Phrases lifted from the actual stages — graph
// retrieval, FAISS search, compaction, tool routing — so the vibe matches
// what's really running on the backend.
const PHRASES = [
  "Recalling memories",
  "Walking the knowledge graph",
  "Probing 2-hop relations",
  "Pulling long-term profile",
  "Embedding the question",
  "Searching FAISS chunks",
  "Reading uploaded files",
  "Cross-referencing facts",
  "Resolving entities",
  "Canonicalizing names",
  "Detecting retractions",
  "Anchoring to user node",
  "Compacting context",
  "Stitching tool results",
  "Routing through the agent graph",
  "Reasoning over evidence",
  "Citing sources",
  "Composing the reply",
];

export default function ThinkingStatus() {
  // Random start so two consecutive turns don't begin on the same phrase.
  const [idx, setIdx] = useState(() =>
    Math.floor(Math.random() * PHRASES.length),
  );
  useEffect(() => {
    const t = setInterval(() => {
      setIdx((i) => (i + 1) % PHRASES.length);
    }, 1600);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="thinking-status">
      <span className="thinking-status-dot" />
      <span className="thinking-status-text" key={idx}>
        {PHRASES[idx]}…
      </span>
    </div>
  );
}
