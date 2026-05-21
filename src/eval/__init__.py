"""GEOS agent evaluation modules.

Ported from the geos_agent repository. Provides:

- ``judge_geos`` — XMLTreeSim headline metric (recursive tree similarity
  with bipartite matching), plus legacy dimension diagnostics.
- ``lxml_xml_eval`` — earlier weighted-dimension reference scorer.
- ``agent_metrics`` — tool error rates and RAG retrieval accuracy from
  agent JSONL/JSON logs.
- ``llm_judge`` — OpenAI/OpenRouter LLM-as-judge evaluator.
- ``token_usage`` — aggregate billed tokens from JSONL/JSON logs.

Contamination helpers live in ``runner.contamination`` — they are used
by the experiment runner, not the post-hoc evaluator.
"""

from . import (
    agent_metrics,
    judge_geos,
    llm_judge,
    lxml_xml_eval,
    token_usage,
)

__all__ = [
    "agent_metrics",
    "judge_geos",
    "llm_judge",
    "lxml_xml_eval",
    "token_usage",
]
