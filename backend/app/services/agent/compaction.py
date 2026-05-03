"""Token-aware context compaction.

Counts tokens with tiktoken using the encoder that matches the configured
chat model (so gpt-5.x → o200k_base, gpt-4 → cl100k_base, etc.). Falls
back to a char/4 heuristic if the encoder fails.

Provides:

  count_tokens(text) → int
  compact_messages(messages, max_tokens) → list[dict]

The compactor preserves:
  - the system message (always)
  - the LAST atomic group at the tail (always — even if it's the only
    thing that fits — because that group carries the most recent user
    context the model needs to answer)
  - as many older groups as fit, walking backwards

CRITICAL: an "atomic group" is either a single non-tool message, OR an
`assistant` message bearing `tool_calls` together with all the `tool`
response messages that follow it. Tool messages MUST stay paired with
their issuing assistant — OpenAI's API rejects an orphan tool message
with `400 messages with role 'tool' must be a response to a preceding
message with 'tool_calls'`. The previous implementation walked
message-by-message and could keep a tool result while dropping its
parent assistant, which crashed long tool-using turns.
"""
from __future__ import annotations

from typing import Iterable

try:
    import tiktoken
except Exception:  # pragma: no cover
    tiktoken = None  # type: ignore[assignment]


def _resolve_encoder():
    """Pick the tiktoken encoder matching the configured chat model.
    For models tiktoken doesn't know yet (gpt-5.x), heuristically pick
    o200k_base, which is correct for the gpt-4o/gpt-5/o-series family.
    Falls back to cl100k_base, then None for the char/4 heuristic.
    Resolution is lazy — runs once on first use after settings load."""
    if tiktoken is None:
        return None
    try:
        from app.config import get_settings

        name = (get_settings().MODEL_NAME or "").lower()
    except Exception:
        name = ""
    if name:
        try:
            return tiktoken.encoding_for_model(name)
        except Exception:
            pass
    if (
        name.startswith("gpt-5")
        or name.startswith("o1")
        or name.startswith("o3")
        or name.startswith("o4")
        or "4o" in name
    ):
        try:
            return tiktoken.get_encoding("o200k_base")
        except Exception:
            pass
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


_ENC = None
_ENC_RESOLVED = False


def _enc():
    global _ENC, _ENC_RESOLVED
    if not _ENC_RESOLVED:
        _ENC = _resolve_encoder()
        _ENC_RESOLVED = True
    return _ENC


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _enc()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Coarse fallback — overestimates a bit which is the safe direction.
    return max(1, len(text) // 4)


def message_tokens(m: dict) -> int:
    """Approx token cost of one chat message (role + content + tool fields).
    Adds a small fixed overhead per message for the role/wrapping."""
    n = 4  # role + separators
    if isinstance(m.get("content"), str):
        n += count_tokens(m["content"])
    elif isinstance(m.get("content"), list):
        for part in m["content"]:
            if isinstance(part, dict) and "text" in part:
                n += count_tokens(part["text"])
    # tool_calls / tool_call_id / name fields:
    for k in ("name", "tool_call_id"):
        v = m.get(k)
        if isinstance(v, str):
            n += count_tokens(v)
    if "tool_calls" in m and isinstance(m["tool_calls"], list):
        for tc in m["tool_calls"]:
            try:
                n += count_tokens(tc.get("function", {}).get("name", ""))
                n += count_tokens(tc.get("function", {}).get("arguments", ""))
            except Exception:
                pass
    return n


def _atomic_groups(messages: list[dict]) -> list[list[dict]]:
    """Partition a message list into atomic units the compactor must keep
    or drop together.

    Why this exists: the OpenAI API requires every `role == "tool"` message
    to be the direct response to an immediately-preceding `assistant`
    message that carries `tool_calls` with matching ids. So an
    `assistant_with_tool_calls` followed by N `tool` messages is ONE
    atomic unit — split it and the next API call 400s.
    """
    groups: list[list[dict]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            # Pull every immediately-following tool response into the group.
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                j += 1
            groups.append(messages[i:j])
            i = j
        else:
            # An orphan `tool` message at the head (shouldn't happen in
            # practice but be defensive) gets its own group too — the
            # compactor will drop it, since it's already invalid input.
            groups.append([m])
            i += 1
    return groups


def compact_messages(messages: list[dict], max_tokens: int) -> list[dict]:
    """Trim a chat-completion message list to fit under `max_tokens`.

    Strategy:
      1. Always keep the leading system message (if present).
      2. Group the rest into atomic units — single messages OR
         (assistant_with_tool_calls + its tool responses) blocks.
      3. Always keep the LAST atomic group (the most recent context the
         model needs to answer). Even if it alone exceeds the budget we
         keep it whole — losing the latest tool round to "save tokens"
         would defeat the point of the turn.
      4. Walk older groups newest-to-oldest, keeping them while they fit.
         Once one doesn't fit, stop — keeping a contiguous recent tail.
      5. If anything was dropped, leave a system note documenting the
         elision so the model knows context was trimmed rather than
         silently forgotten.
    """
    if not messages:
        return messages
    sys_msgs: list[dict] = []
    rest: list[dict] = []
    for m in messages:
        if m.get("role") == "system" and not sys_msgs:
            sys_msgs.append(m)
        else:
            rest.append(m)

    if not rest:
        return sys_msgs

    groups = _atomic_groups(rest)
    last_group = groups[-1]
    middle_groups = groups[:-1]

    base_tokens = sum(message_tokens(m) for m in sys_msgs) + sum(
        message_tokens(m) for m in last_group
    )
    budget = max_tokens - base_tokens
    kept_groups_reversed: list[list[dict]] = []
    for grp in reversed(middle_groups):
        cost = sum(message_tokens(m) for m in grp)
        if cost <= budget:
            budget -= cost
            kept_groups_reversed.append(grp)
        else:
            break

    total_middle = sum(len(g) for g in middle_groups)
    kept_middle = sum(len(g) for g in kept_groups_reversed)
    dropped = total_middle - kept_middle

    out = list(sys_msgs)
    if dropped > 0:
        out.append(
            {
                "role": "system",
                "content": (
                    f"[Note: {dropped} earlier message(s) were elided to keep the "
                    f"context within the token budget. The thread summary already "
                    f"captures their gist.]"
                ),
            }
        )
    for grp in reversed(kept_groups_reversed):
        out.extend(grp)
    out.extend(last_group)
    return out


def total_tokens(messages: Iterable[dict]) -> int:
    return sum(message_tokens(m) for m in messages)
