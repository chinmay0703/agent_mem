SUMMARIZATION_SYSTEM = """You compress a chat thread into a short rolling summary.

Rules:
- Output a single paragraph, <= 120 words.
- Preserve: the user's intent, open threads, decisions made, and any facts the user
  has just shared about themselves.
- Drop: pleasantries, repeated content, and detail that is already captured in
  the prior summary.
- Write in third person ("the user ...", "the assistant ..."). No bullet lists.
- Output the summary text only. No preface.
"""

SUMMARIZATION_USER = """Prior summary:
---
{prior_summary}
---

New turns since prior summary:
---
{new_turns}
---

Updated summary:"""
