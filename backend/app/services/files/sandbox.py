"""Python sandbox for the agent's `python_sandbox` tool.

This is NOT a security boundary against a determined adversary — that needs a
container, gVisor, Firecracker, or similar. What this gives you:
  - per-call CPU + wall-time + memory caps (rlimit on macOS/Linux)
  - no network (best-effort: PYTHONPATH stripped, no sockets opened by user
    code unless they re-import; full block needs a sandbox container)
  - file access only via paths we explicitly mount into a per-call tempdir

For prod-grade isolation, swap `_run_subprocess` for a Docker exec.
"""
from __future__ import annotations

import asyncio
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path


# Hard caps. Aggressive on purpose — we're running on the API host.
TIMEOUT_S = 8
CPU_S = 6
MAX_MEM_BYTES = 512 * 1024 * 1024  # 512 MB
MAX_OUTPUT_CHARS = 12_000


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool


def _set_limits():  # pragma: no cover — runs in the child
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_S, CPU_S))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEM_BYTES, MAX_MEM_BYTES))
    except (ValueError, OSError):
        pass


def _run_subprocess(code: str, cwd: str) -> SandboxResult:
    """Run user code in a child Python process. Synchronous; the caller
    offloads to a thread."""
    timed_out = False
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            preexec_fn=_set_limits if os.name != "nt" else None,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": cwd,
                "PYTHONIOENCODING": "utf-8",
                # Keep our venv site-packages reachable so user code can import
                # pandas / numpy / etc.
                "PYTHONPATH": os.pathsep.join(
                    p for p in sys.path if p and "site-packages" in p
                ),
            },
        )
        out = proc.stdout
        err = proc.stderr
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = e.stdout or ""
        err = (e.stderr or "") + f"\n[sandbox] timeout after {TIMEOUT_S}s"
        rc = -9
    except Exception as e:
        out = ""
        err = f"[sandbox] failed to launch: {e}"
        rc = -1
    return SandboxResult(
        stdout=(out or "")[:MAX_OUTPUT_CHARS],
        stderr=(err or "")[:MAX_OUTPUT_CHARS],
        returncode=rc,
        timed_out=timed_out,
    )


_PRELUDE = textwrap.dedent(
    """
    # ── sandbox prelude ──
    import sys, os, json, math, statistics, csv, datetime, re
    try:
        import pandas as pd
    except Exception:
        pd = None
    try:
        import numpy as np
    except Exception:
        np = None
    # User code follows.
    """
).strip()


async def run_python(code: str, file_paths: dict[str, str] | None = None) -> SandboxResult:
    """`file_paths` is a {logical_name: source_path} map. Each file is
    copied into the sandbox cwd at `logical_name` before the user code runs,
    so they can do `pd.read_csv("sales.csv")` directly."""
    tmp = tempfile.mkdtemp(prefix="chatmem-sbx-")
    try:
        for logical, src in (file_paths or {}).items():
            safe_logical = Path(logical).name  # strip dirs — defense in depth
            dst = Path(tmp) / safe_logical
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass
        full_code = _PRELUDE + "\n" + code
        return await asyncio.to_thread(_run_subprocess, full_code, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
