import React, { useEffect, useRef, useState } from "react";
import { sendMessage, getThread, MAX_MESSAGE_CHARS } from "../../api/client.js";
import MarkdownView from "./MarkdownView.jsx";
import MessageMeta from "./MessageMeta.jsx";
import ThinkingTimeline from "./ThinkingTimeline.jsx";
import ThinkingStatus from "./ThinkingStatus.jsx";
import { useToast } from "../Toast.jsx";

const COMPOSER_MAX_PX = 200;

export default function ChatInterface({
  userId,
  activeThreadId,
  onTurnComplete,
  onThreadCreated,
}) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const scrollerRef = useRef(null);
  const textareaRef = useRef(null);
  const abortRef = useRef(null);
  const toast = useToast();

  // Load thread history whenever the active thread or user changes.
  // Also abort any in-flight chat request — its response would otherwise
  // land in state that now belongs to a different thread.
  useEffect(() => {
    let cancel = false;
    async function load() {
      setError(null);
      if (!activeThreadId) {
        setMessages([]);
        return;
      }
      setLoadingHistory(true);
      try {
        const detail = await getThread(userId, activeThreadId);
        if (cancel) return;
        const restored = detail.messages.map((m) => ({
          role: m.role === "assistant" ? "bot" : "user",
          content: m.content,
          meta:
            m.role === "assistant" && m.metadata
              ? {
                  added: m.metadata.added || [],
                  updated: m.metadata.updated || [],
                  reinforced: m.metadata.reinforced || [],
                  removed: m.metadata.removed || [],
                  toolCalls: m.metadata.tool_calls || [],
                  sources: m.metadata.sources || [],
                  turnMs: m.metadata.turn_ms || 0,
                }
              : undefined,
        }));
        setMessages(restored);
      } catch (e) {
        if (!cancel) setMessages([]);
      } finally {
        if (!cancel) setLoadingHistory(false);
      }
    }
    load();
    return () => {
      cancel = true;
      abortRef.current?.abort();
    };
  }, [userId, activeThreadId]);

  // Final-unmount safety net: tear down any still-running fetch so its
  // setState doesn't fire on an unmounted component.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Auto-scroll the message list to the bottom on new content.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  // Auto-resize the composer textarea up to a max height; scroll past that.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_PX)}px`;
  }, [draft]);

  async function send(textOverride, opts = {}) {
    const text = (textOverride ?? draft).trim();
    if (!text || pending) return;
    if (text.length > MAX_MESSAGE_CHARS) {
      setError(
        `Message is ${text.length} chars; cap is ${MAX_MESSAGE_CHARS}. Trim it and try again.`,
      );
      return;
    }
    if (textOverride === undefined) setDraft("");
    setError(null);
    const optimistic = { role: "user", content: text };
    setMessages((m) => [...m, optimistic]);
    setPending(true);
    abortRef.current = new AbortController();
    try {
      const res = await sendMessage(
        {
          userId,
          threadId: activeThreadId,
          message: text,
          regenerate: !!opts.regenerate,
        },
        { signal: abortRef.current.signal },
      );
      if (!activeThreadId && res.thread_id) {
        onThreadCreated?.(res.thread_id);
      }
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          content: res.response,
          meta: {
            added: res.added || [],
            updated: res.updated || [],
            reinforced: res.reinforced || [],
            removed: res.removed || [],
            toolCalls: res.tool_calls || [],
            sources: res.sources || [],
            turnMs: res.turn_ms || 0,
          },
        },
      ]);
      onTurnComplete?.();
    } catch (e) {
      if (e.name === "AbortError" || e.message === "aborted") {
        // Roll back the optimistic user message; user explicitly cancelled.
        setMessages((m) => m.slice(0, -1));
        toast.info("Cancelled");
      } else {
        setError(e.message || "Request failed");
        setMessages((m) => [
          ...m,
          { role: "bot", content: "Sorry, something went wrong.", error: true },
        ]);
      }
    } finally {
      abortRef.current = null;
      setPending(false);
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    } else if (e.key === "Escape") {
      setDraft("");
    }
  }

  async function copyMessage(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied");
    } catch (e) {
      toast.error("Copy failed");
    }
  }

  function regenerate() {
    // Find the most recent user message; resend it with regenerate=true so
    // the backend prunes the prior user+assistant pair from Postgres + the
    // chunk index before running the agent. Without that flag the DB ends
    // up with two user rows for one displayed turn.
    const lastUserIdx = [...messages].map((m) => m.role).lastIndexOf("user");
    if (lastUserIdx < 0) return;
    const text = messages[lastUserIdx].content;
    setMessages((m) => m.slice(0, lastUserIdx));
    send(text, { regenerate: true });
  }

  return (
    <div className="chat">
      <div className="messages" ref={scrollerRef}>
        {loadingHistory && (
          <div className="empty-state">
            <span className="typing">
              <span /><span /><span />
            </span>
          </div>
        )}
        {!loadingHistory && messages.length === 0 && !pending && (
          <div className="empty-state">
            <h3>Say hi</h3>
            <p>
              Tell the bot about yourself — your role, employer, goals,
              preferences, plans. Upload a file in the Files tab and ask
              questions about it. Watch the memory graph fill in on the right.
            </p>
          </div>
        )}
        {messages.map((m, i) => {
          const isLastBot =
            m.role === "bot" && i === messages.length - 1 && !pending;
          return (
            <div key={i} className={`msg ${m.role}`}>
              {m.role === "bot" && m.meta?.toolCalls?.length > 0 && (
                <ThinkingTimeline
                  steps={m.meta.toolCalls}
                  totalMs={m.meta.turnMs || 0}
                />
              )}
              {m.role === "bot" ? (
                <MarkdownView sources={m.meta?.sources || []}>
                  {m.content}
                </MarkdownView>
              ) : (
                <div>{m.content}</div>
              )}
              {m.role === "bot" && !m.error && (
                <div className="msg-actions">
                  <button
                    className="msg-action"
                    onClick={() => copyMessage(m.content)}
                    title="Copy reply"
                  >
                    Copy
                  </button>
                  {isLastBot && (
                    <button
                      className="msg-action"
                      onClick={regenerate}
                      title="Regenerate this reply"
                    >
                      Regenerate
                    </button>
                  )}
                </div>
              )}
              {m.meta && (
                <MessageMeta
                  added={m.meta.added}
                  updated={m.meta.updated}
                  reinforced={m.meta.reinforced}
                  removed={m.meta.removed}
                  toolCalls={m.meta.toolCalls}
                  sources={m.meta.sources}
                />
              )}
            </div>
          );
        })}
        {pending && (
          <div className="msg bot">
            <ThinkingStatus />
          </div>
        )}
        {error && (
          <div className="msg bot" style={{ color: "var(--danger)" }}>
            {error}
          </div>
        )}
      </div>
      <div className="composer">
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder="Type a message... (Enter to send · Shift+Enter for newline · Esc to clear)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={pending}
          maxLength={MAX_MESSAGE_CHARS}
        />
        {pending ? (
          <button
            className="btn btn-danger-outline"
            onClick={stop}
            title="Cancel this request"
          >
            Stop
          </button>
        ) : (
          <button
            className="btn btn-primary"
            onClick={() => send()}
            disabled={!draft.trim()}
            title={
              draft.length > MAX_MESSAGE_CHARS * 0.9
                ? `${draft.length} / ${MAX_MESSAGE_CHARS}`
                : "Send"
            }
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}
