"""kindle-status — mirror live agent activity into a small JSON file.

Observer-only hooks (never veto, never raise). The Kindle dashboard server
reads ~/.hermes/plugin-data/kindle-status/live.json (mounted read-only) to show
which session/platform is working, which tool is running, and whether a turn
is blocked waiting for an approval or an answer from the user.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger("hermes_plugins.kindle_status")

_DIR = os.path.join(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")), "plugin-data", "kindle-status")
_PATH = os.path.join(_DIR, "live.json")
_lock = threading.Lock()
_sessions: dict[str, dict] = {}
_MAX = 24
_STALE = 6 * 3600


def _sid(kw: dict) -> str:
    return str(kw.get("session_id") or kw.get("parent_session_id") or kw.get("task_id") or "unknown")


def _short(v: Any, n: int = 80) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _flush() -> None:
    now = time.time()
    for k in [k for k, v in _sessions.items() if now - v.get("updated", 0) > _STALE]:
        _sessions.pop(k, None)
    if len(_sessions) > _MAX:
        for k, _ in sorted(_sessions.items(), key=lambda kv: kv[1].get("updated", 0))[: len(_sessions) - _MAX]:
            _sessions.pop(k, None)
    os.makedirs(_DIR, exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"updated": now, "pid": os.getpid(), "sessions": _sessions}, f, ensure_ascii=False)
    os.replace(tmp, _PATH)


def _update(kw: dict, **fields: Any) -> None:
    try:
        with _lock:
            s = _sessions.setdefault(_sid(kw), {})
            if kw.get("platform"):
                s["platform"] = str(kw["platform"])
            if kw.get("model"):
                s["model"] = str(kw["model"]).rsplit("/", 1)[-1]
            if fields.get("state") and fields["state"] != s.get("state"):
                s["since"] = time.time()
            s.update(fields)
            s["updated"] = time.time()
            _flush()
    except Exception as exc:  # observers must never break a turn
        logger.debug("kindle-status: update failed: %s", exc)


def _approval_text(kw: dict) -> str:
    for key in ("command", "description", "reason", "prompt", "summary", "tool_name"):
        if kw.get(key):
            return _short(kw[key])
    args = kw.get("args") or kw.get("tool_input") or {}
    if isinstance(args, dict) and args.get("command"):
        return _short(args["command"])
    return ""


def _on_pre_llm_call(**kw: Any) -> None:
    _update(kw, state="running", tool="", detail="")


def _on_pre_tool_call(**kw: Any) -> None:
    _update(kw, state="running", tool=str(kw.get("tool_name") or ""), tool_started=time.time())


def _on_post_tool_call(**kw: Any) -> None:
    _update(kw, state="running", tool="", last_tool=str(kw.get("tool_name") or ""))


def _on_pre_approval(**kw: Any) -> None:
    _update(kw, state="approval", detail=_approval_text(kw))


def _on_post_approval(**kw: Any) -> None:
    _update(kw, state="running", detail="", last_approval=str(kw.get("choice") or kw.get("decision") or kw.get("approved") or ""))


def _on_input_request(**kw: Any) -> None:
    _update(kw, state="input", detail=_short(kw.get("question") or kw.get("prompt") or ""))


def _on_input_resolved(**kw: Any) -> None:
    _update(kw, state="running", detail="")


def _on_loop_stopped(**kw: Any) -> None:
    _update(kw, state="idle", tool="", detail="")


def register(ctx: Any) -> None:
    for name, fn in (
        ("pre_llm_call", _on_pre_llm_call),
        ("pre_tool_call", _on_pre_tool_call),
        ("post_tool_call", _on_post_tool_call),
        ("pre_approval_request", _on_pre_approval),
        ("post_approval_response", _on_post_approval),
        ("on_human_input_request", _on_input_request),
        ("on_human_input_resolved", _on_input_resolved),
        ("agent_loop_stopped", _on_loop_stopped),
        ("on_session_end", _on_loop_stopped),
    ):
        ctx.register_hook(name, fn)
    logger.info("kindle-status: writing %s", _PATH)
