# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp>=1.0.0,<2",
#   "requests>=2.31",
#   "numpy>=1.26",
# ]
# ///
"""MCP server exposing RB-style reasoning-memory items retrieved via OpenRouter embeddings.

This is the **M3** MCP (tool-locus) variant from D-008. It serves the same
reasoning-memory items as M4-g (external-inject variant) but through an in-run
MCP tool rather than primer injection.

Hard guarantees (RN-003 P2 #8):
- Reads ``OPENROUTER_API_KEY`` at startup. Missing key → ``sys.exit(1)``.
  No fallback to OpenAI, no fallback to lexical.
- Preflight: makes one test embedding call on startup. Non-200 → exit 1.
- Per-query failures surface as explicit tool errors, not empty-result silent
  degrades.
- Pure cosine similarity (no post-multiply by past-task treesim — see
  RN-003 P3 #9).

Environment:
  OPENROUTER_API_KEY           — required
  OPENROUTER_API_BASE          — default https://openrouter.ai/api/v1
  MEMORY_EMBED_MODEL           — default qwen/qwen3-embedding-8b
  MEMORY_ITEMS_PATH            — default /plugins/repo3/memory_items.json
  MEMORY_EMBED_INDEX_PATH      — default /plugins/repo3/memory_items_embeddings.json

The items JSON must be a list of dicts matching the ReasoningBank schema
(see ``misc/memory_artifacts/M4-g/items.json``):
    [{"title": str, "description": str, "solver_family": str,
      "kind": str, "abstraction_level": str, "applies_when": str,
      "content": str}, ...]

The embedding index is a parallel JSON list of {"idx": i, "embedding": [...]}.
It is built once by ``scripts/memory/build_items_embedding_index.py`` at
offline-prep time and mounted read-only at test time.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import numpy as np  # type: ignore
except ImportError as _np_err:
    import sys as _sys
    print(f"memory_mcp_embed: FATAL: numpy not available ({_np_err}). "
          f"uv run should install it per the script header. "
          f"If running outside uv, `pip install numpy`.", file=_sys.stderr)
    _sys.exit(1)
import requests
from mcp.server.fastmcp import FastMCP


DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_EMBED_MODEL = "qwen/qwen3-embedding-8b"
DEFAULT_ITEMS_PATH = Path(os.environ.get(
    "MEMORY_ITEMS_PATH", "/plugins/repo3/memory_items.json"))
DEFAULT_INDEX_PATH = Path(os.environ.get(
    "MEMORY_EMBED_INDEX_PATH", "/plugins/repo3/memory_items_embeddings.json"))

API_KEY = os.environ.get("OPENROUTER_API_KEY")
API_BASE = os.environ.get("OPENROUTER_API_BASE", DEFAULT_API_BASE)
EMBED_MODEL = os.environ.get("MEMORY_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def _fatal(msg: str) -> None:
    print(f"memory_mcp_embed: FATAL: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


if not API_KEY:
    _fatal("OPENROUTER_API_KEY not set. No lexical fallback; exiting.")


def _embed(text: str) -> np.ndarray:
    resp = requests.post(
        f"{API_BASE}/embeddings",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": EMBED_MODEL, "input": text},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"embed call failed {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return np.asarray(data["data"][0]["embedding"], dtype=np.float32)


# Preflight
try:
    _test_vec = _embed("test")
    print(f"memory_mcp_embed: preflight OK, embed dim={len(_test_vec)}", file=sys.stderr, flush=True)
except Exception as exc:
    _fatal(f"preflight embed call failed: {exc}")


# Load items
if not DEFAULT_ITEMS_PATH.exists():
    _fatal(f"items file not found: {DEFAULT_ITEMS_PATH}")
try:
    ITEMS: list[dict[str, Any]] = json.loads(DEFAULT_ITEMS_PATH.read_text())
    if not isinstance(ITEMS, list):
        _fatal(f"items file is not a list: {DEFAULT_ITEMS_PATH}")
except Exception as exc:
    _fatal(f"items parse failed: {exc}")

# Load embedding index
if not DEFAULT_INDEX_PATH.exists():
    _fatal(f"embedding index not found: {DEFAULT_INDEX_PATH}. "
           f"Build via scripts/memory/build_items_embedding_index.py.")
try:
    INDEX_RAW = json.loads(DEFAULT_INDEX_PATH.read_text())
    EMBEDDINGS = np.asarray([e["embedding"] for e in INDEX_RAW], dtype=np.float32)
except Exception as exc:
    _fatal(f"embedding index load failed: {exc}")

if len(EMBEDDINGS) != len(ITEMS):
    _fatal(f"embedding/items length mismatch: {len(EMBEDDINGS)} vs {len(ITEMS)}")

# L2-normalize once
_norms = np.linalg.norm(EMBEDDINGS, axis=1, keepdims=True)
_norms[_norms == 0] = 1.0
EMBEDDINGS_N = EMBEDDINGS / _norms

print(f"memory_mcp_embed: loaded {len(ITEMS)} items; embedding index dim={EMBEDDINGS.shape[1]}",
      file=sys.stderr, flush=True)


app = FastMCP("memory")


@app.tool()
def memory_lookup(query: str, n: int = 3) -> dict[str, Any]:
    """Retrieve reasoning-memory items most similar to the given query.

    Memory items are **abstract cross-task rules and anti-patterns** distilled
    from past agent trajectories. Each item is actionable on its own without
    reference to any specific past task. Use this tool for:
    - Solver selection: how physics description maps to solver classes
    - Schema hygiene: element names NOT to invent (common hallucinations)
    - Attribute rules that apply across tasks in a physics family

    Args:
      query: brief natural-language description of the current task's
        physics or the design decision you are deliberating. Examples:
        "hydraulic fracture propagation solver setup",
        "contact mechanics for casing-cement interface".
      n: max number of items to return (1..6).

    Returns:
      Dict with `results` — a list of items ranked by cosine similarity.
    """
    n = max(1, min(n, 6))
    try:
        q_vec = _embed(query)
    except Exception as exc:
        # EXPLICIT error — no silent empty-result degrade. Agent sees this.
        return {
            "query": query,
            "error": f"embedding call failed: {exc}",
            "results": [],
        }
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0:
        return {"query": query, "error": "zero-norm query embedding", "results": []}
    q_vec_n = q_vec / q_norm
    sims = EMBEDDINGS_N @ q_vec_n  # cosine
    top_idx = np.argsort(-sims)[:n]
    return {
        "query": query,
        "n_items_total": len(ITEMS),
        "results": [
            {
                "similarity": float(sims[i]),
                "title": ITEMS[int(i)].get("title"),
                "description": ITEMS[int(i)].get("description"),
                "solver_family": ITEMS[int(i)].get("solver_family"),
                "kind": ITEMS[int(i)].get("kind"),
                "abstraction_level": ITEMS[int(i)].get("abstraction_level"),
                "applies_when": ITEMS[int(i)].get("applies_when"),
                "content": ITEMS[int(i)].get("content"),
            }
            for i in top_idx
        ],
    }


@app.tool()
def memory_stats() -> dict[str, Any]:
    """Report how many items are loaded and the embedding dim."""
    return {
        "n_items": len(ITEMS),
        "embedding_dim": int(EMBEDDINGS.shape[1]),
        "embed_model": EMBED_MODEL,
    }


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        q = "hydraulic fracture"
        r = memory_lookup(q, 2)
        print(json.dumps(r, indent=2)[:800])
        sys.exit(0)
    app.run()
