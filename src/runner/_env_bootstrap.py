"""Environment-variable bootstrap that must run on package import.

Many call sites (in this package and downstream tools that simply import
``runner``) depend on the side effects below. Keep the logic identical
to the original ``scripts/run_experiment.py`` lines 84-85.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Claude Code authenticates to OpenRouter via ANTHROPIC_AUTH_TOKEN (paired with
# ANTHROPIC_BASE_URL=https://openrouter.ai/api). If only OPENROUTER_API_KEY is
# set (common in .env files), promote it so the same key works for both the
# Claude CLI and the MCP server's embedding calls.
if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
    os.environ["ANTHROPIC_AUTH_TOKEN"] = os.environ["OPENROUTER_API_KEY"]
