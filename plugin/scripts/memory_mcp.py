# /// script
# dependencies = [
#   "mcp>=1.0.0,<2",
# ]
# ///
"""MCP server exposing a frozen G-Memory-lite index built from past trajectories.

The memory is a JSON file at $MEMORY_INDEX_PATH (default:
/plugins/repo3/memory_index.json) containing entries with:
  { task_id, instructions_excerpt, final_treesim,
    reference_xmls (paths in /geos_lib), productive_rag_queries,
    topic_keywords, section_strengths }

Exposes one tool: `memory_lookup(query, n=3)` that scores each memory entry
by keyword overlap with the query, returns the top n with their reference
XML paths + a short rationale.

This is FROZEN — no mutation at test time. Parallelism-safe.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


DEFAULT_INDEX_PATH = Path(os.environ.get(
    "MEMORY_INDEX_PATH",
    "/plugins/repo3/memory_index.json",
))


def _tokenize(text: str) -> list[str]:
    # Split camelCase first, then lowercase + split non-alpha
    text = re.sub(r"([A-Z])", r" \1", text)
    words = re.findall(r"[a-z]+", text.lower())
    stop = set("a an and are as at be by for for from has have in into is it its of on or that the to was were will with this these those we you your if not".split())
    return [w for w in words if len(w) > 3 and w not in stop]


def _load_index(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"memory_mcp: index not found at {path}", file=sys.stderr)
        return []
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    return data.get("tasks", [])


INDEX: list[dict[str, Any]] = _load_index(DEFAULT_INDEX_PATH)
print(f"memory_mcp: loaded {len(INDEX)} memory entries from {DEFAULT_INDEX_PATH}", file=sys.stderr)


app = FastMCP("memory")


def _score(query_tokens: list[str], entry: dict[str, Any]) -> float:
    """Keyword overlap between query and entry topic_keywords + task_id tokens."""
    tgt_tokens = set(entry.get("topic_keywords", []))
    tgt_tokens.update(_tokenize(entry.get("task_id", "")))
    tgt_tokens.update(_tokenize(entry.get("instructions_excerpt", "")))
    qset = set(query_tokens)
    if not tgt_tokens or not qset:
        return 0.0
    overlap = tgt_tokens & qset
    if not overlap:
        return 0.0
    # Weight by final_treesim so higher-quality past tasks rank higher.
    score = len(overlap) / max(1, len(qset))
    treesim_weight = float(entry.get("final_treesim") or 0.5)
    return score * (0.5 + treesim_weight)


@app.tool()
def memory_lookup(query: str, n: int = 3, min_score: float = 0.6) -> dict[str, Any]:
    """Retrieve past task memories most similar to the given query, ONLY if a
    confident match exists.

    IMPORTANT: This tool is a SAFETY NET, not a primary search tool. Use it
    only after you have already issued 2-3 semantic RAG queries via
    mcp__geos-rag__search_navigator / search_schema / search_technical and
    have NOT yet found a clearly-matching reference XML. Calling this tool
    early biases your exploration toward past tasks whose physics may not
    match the current task.

    The tool enforces a minimum confidence threshold (default 0.6). If no
    past task matches the query above this threshold, you get a "no match"
    response — that is the correct behavior; it means you should rely on
    semantic RAG, not memory. Past attempts to weaken this threshold have
    led to anchoring on wrong-physics-family priors (e.g., hydrofracture
    examples returned for embedded-fracture tasks).

    When you DO get matches, treat them as one possible reference, not a
    final answer. Always verify the match's physics family (solver,
    constitutive model, mesh type) actually matches your current task
    before adapting its reference_xmls.

    Args:
      query: short natural-language description of the current task's physics
        and goal. Be specific about solver family if known.
      n: max number of memories to return (1..5).
      min_score: minimum match-score threshold (default 0.6). Increase to
        0.8 for high-confidence-only matches.

    Returns:
      Dict with `results` (possibly empty) and a `note` field. If no result
      meets min_score, results is empty and note explains why.
    """
    n = max(1, min(n, 5))
    q_tokens = _tokenize(query)
    scored = [(s, e) for e in INDEX if (s := _score(q_tokens, e)) > 0]
    scored.sort(key=lambda x: -x[0])
    above = [(s, e) for s, e in scored if s >= min_score]
    top = above[:n]
    if not top:
        best_seen = scored[0][0] if scored else 0.0
        return {
            "query": query,
            "results": [],
            "note": (
                f"No past task matched the query above threshold "
                f"min_score={min_score} (best seen score: {best_seen:.3f}). "
                "This means none of the {} past training tasks is a confident "
                "match for the current task's physics. **Rely on semantic RAG "
                "(mcp__geos-rag__search_*) to discover the right reference XML "
                "from /geos_lib/inputFiles/ instead.**"
            ).format(len(INDEX)),
        }
    return {
        "query": query,
        "n_above_threshold": len(above),
        "min_score_used": min_score,
        "results": [
            {
                "task_id": e.get("task_id"),
                "final_treesim": e.get("final_treesim"),
                "match_score": round(s, 3),
                "reference_xmls": e.get("reference_xmls", [])[:5],
                "productive_rag_queries": e.get("productive_rag_queries", [])[:4],
                "section_strengths": e.get("section_strengths", {}),
                "instructions_excerpt": (e.get("instructions_excerpt") or "")[:240],
                "verify_physics_match": (
                    "Before adapting these reference_xmls, verify the past "
                    "task's solver family and mesh type match your current task."
                ),
            }
            for s, e in top
        ],
    }


@app.tool()
def memory_stats() -> dict[str, Any]:
    """Report how many past-task memories are loaded."""
    return {
        "n_memories": len(INDEX),
        "index_path": str(DEFAULT_INDEX_PATH),
        "has_index": DEFAULT_INDEX_PATH.exists(),
    }


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        print(json.dumps({"loaded": len(INDEX), "path": str(DEFAULT_INDEX_PATH)}))
        sys.exit(0)
    app.run()
