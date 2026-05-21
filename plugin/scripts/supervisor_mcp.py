# /// script
# dependencies = [
#   "mcp>=1.0.0,<2",
#   "openai>=1.40",
# ]
# ///
"""MCP server: simulated human supervisor.

Exposes a single tool, `consult_supervisor(question)`. The tool
synchronously calls a small LLM (default deepseek-v4-flash) that has
read access to the FULL original simulation specification for the
current task. The LLM is instructed to answer concisely using only
information present in the spec, and never to volunteer information
the agent did not ask about.

Spec source:
  Path is read from the env var SUPERVISOR_SPEC_PATH. The file is
  loaded once at process start and held only in this process's memory.
  Set in mcp-config so it does not appear in the docker `-e` list, and
  mounted at a path outside `/workspace`.

LLM endpoint:
  OpenAI-compatible. Defaults: base_url=https://api.deepseek.com/v1,
  model=deepseek-v4-flash. Override via SUPERVISOR_LLM_BASE_URL,
  SUPERVISOR_LLM_MODEL. API key from SUPERVISOR_LLM_API_KEY (preferred)
  or DEEPSEEK_API_KEY.

Telemetry:
  Every call appends a JSON line to /workspace/supervisor_calls.jsonl
  (if /workspace exists). Records the question, answer, latency, and
  token counts. The agent does not see this file.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from openai import OpenAI


SPEC_PATH = Path(os.environ.get("SUPERVISOR_SPEC_PATH", "/supervisor/spec.md"))
LOG_PATH = Path(os.environ.get(
    "SUPERVISOR_LOG_PATH",
    "/workspace/supervisor_calls.jsonl",
))
TASK_NAME = os.environ.get("SUPERVISOR_TASK_NAME", "")
MODEL = os.environ.get("SUPERVISOR_LLM_MODEL", "deepseek-v4-flash")
BASE_URL = os.environ.get(
    "SUPERVISOR_LLM_BASE_URL", "https://api.deepseek.com/v1"
)
API_KEY = (
    os.environ.get("SUPERVISOR_LLM_API_KEY")
    or os.environ.get("DEEPSEEK_API_KEY")
    or ""
)


def _load_spec() -> str:
    if not SPEC_PATH.exists():
        print(
            f"supervisor_mcp: WARNING — spec not found at {SPEC_PATH}",
            file=sys.stderr,
        )
        return ""
    return SPEC_PATH.read_text()


SPEC_TEXT = _load_spec()
print(
    f"supervisor_mcp: loaded {len(SPEC_TEXT)} chars of spec from {SPEC_PATH}",
    file=sys.stderr,
)
print(f"supervisor_mcp: model={MODEL} base_url={BASE_URL}", file=sys.stderr)


SYSTEM_PROMPT = """\
You are the human researcher who wrote the simulation specification
shown below. An AI assistant is helping you author the simulation
inputs. The assistant only saw a relaxed version of your spec — some
details were intentionally omitted from what they received.

When the assistant asks you a question:
- Answer concisely (1–3 sentences).
- Use only information that is present in the specification. If the
  answer is not in the specification, say so plainly. Do not invent.
- DO NOT volunteer information the assistant did not ask about. Stay
  on the question.
- Do not name GEOS XML tag or attribute names unless the assistant
  named them first; speak in the same scientific language the
  specification uses.
- If the question is vague, ask one short clarifying question rather
  than guessing.

Original specification:
---
{spec}
---
"""


def _client() -> OpenAI:
    if not API_KEY:
        raise RuntimeError(
            "supervisor_mcp: no API key (SUPERVISOR_LLM_API_KEY or "
            "DEEPSEEK_API_KEY)"
        )
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def _log(payload: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as e:
        print(f"supervisor_mcp: log write failed: {e}", file=sys.stderr)


app = FastMCP("geos-supervisor")


@app.tool()
def consult_supervisor(question: str) -> dict[str, Any]:
    """Ask a clarifying question to the human researcher.

    Use this when the simulation specification you received does not
    contain a value or detail you need to make a faithful XML, and you
    cannot reasonably infer it from GEOS conventions or domain
    knowledge. Each call costs the researcher's time, so prefer to
    infer when you can. The researcher will answer concisely using
    only information in their original specification.

    Args:
        question: A short, specific question. State the parameter or
            choice you are asking about.

    Returns:
        {"answer": <str>, "latency_seconds": <float>}
    """
    started = time.time()
    if not SPEC_TEXT:
        return {
            "answer": "(no specification available — supervisor cannot answer)",
            "latency_seconds": 0.0,
        }
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=0.0,
            max_tokens=1500,
            messages=[
                {"role": "system",
                 "content": SYSTEM_PROMPT.format(spec=SPEC_TEXT)},
                {"role": "user", "content": question},
            ],
        )
        msg = resp.choices[0].message
        answer = msg.content or ""
        # DSv4 returns CoT in reasoning_content; merge if content is empty
        if not answer.strip():
            rc = getattr(msg, "reasoning_content", None) or ""
            if rc:
                # take only the post-reasoning summary, or fall back to last
                # 400 chars of reasoning if no clean separator
                answer = rc.strip().split("\n\n")[-1][:600] or rc[-600:]
        usage = resp.usage
        latency = time.time() - started
        _log({
            "ts": _dt.datetime.utcnow().isoformat() + "Z",
            "task": TASK_NAME,
            "question": question,
            "answer": answer,
            "latency_seconds": round(latency, 3),
            "model": MODEL,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        })
        return {"answer": answer, "latency_seconds": round(latency, 3)}
    except Exception as e:
        _log({
            "ts": _dt.datetime.utcnow().isoformat() + "Z",
            "task": TASK_NAME,
            "question": question,
            "error": str(e),
            "trace": traceback.format_exc(limit=3),
        })
        return {
            "answer": f"(supervisor error — {e})",
            "latency_seconds": round(time.time() - started, 3),
        }


@app.tool()
def supervisor_stats() -> dict[str, Any]:
    """Report whether the supervisor channel is loaded and ready."""
    return {
        "spec_path": str(SPEC_PATH),
        "spec_loaded": bool(SPEC_TEXT),
        "spec_chars": len(SPEC_TEXT),
        "model": MODEL,
        "base_url": BASE_URL,
        "task": TASK_NAME,
    }


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        print(json.dumps({
            "spec_loaded": bool(SPEC_TEXT),
            "spec_chars": len(SPEC_TEXT),
            "spec_path": str(SPEC_PATH),
            "model": MODEL,
        }))
        sys.exit(0)
    app.run()
