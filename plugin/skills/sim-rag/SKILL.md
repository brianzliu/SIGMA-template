---
name: {{SLUG}}-rag
description: Use when answering {{SIMULATOR_NAME}} documentation, syntax, or schema questions with the plugin-provided {{RAG_TOOL_NAMES}} MCP tools.
---

Use the {{SIMULATOR_NAME}} RAG MCP tools before answering questions about
{{SIMULATOR_NAME}} {{CONFIG_LABEL}} syntax, examples, or documentation.

The {{SIMULATOR_NAME}} primer is normally injected into the agent system
context by the experiment runner. Treat the system-provided primer as the
high-level orientation for the task, then use the RAG tools for
task-specific evidence and exact details.

## Tool selection

The three collections defined in `configs/simulator.yaml` (default:
navigator / schema / technical) are surfaced as the MCP tools
`{{RAG_TOOL_NAMES}}`. Use them as follows:

- Conceptual / discovery search — when the relevant feature or solver is unclear.
- Authoritative element / API search — before writing or changing attributes.
- Concrete example search — to mirror working {{CONFIG_LABEL}} structure.

When a result returns a source reference (path + line range), read the
referenced file if the host environment provides file-reading tools.

The ChromaDB location is configured by `{{ENV_PREFIX}}_VECTOR_DB_DIR` and
defaults to `{{VECTOR_DB_DIR}}` in this plugin.
