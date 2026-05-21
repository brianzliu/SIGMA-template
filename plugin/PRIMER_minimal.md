# {{SIMULATOR_NAME}} Primer (minimal)

{{SIMULATOR_NAME}} is a {{TAGLINE}}. Tasks require authoring a {{CONFIG_LABEL}}.

## Where things live (inside the container)

- `{{CONTAINER_LIB_DIR}}/` — the {{SIMULATOR_NAME}} source repository, including validated examples and documentation. Treat these as the authoritative reference.
- `/workspace/inputs/` — where you must write the final output. Your output goes here.

## Top-level structure

> **Adapt this section to your simulator.** Document the canonical skeleton
> of a valid {{CONFIG_LABEL}} here — top-level blocks, required headers, etc.
> For GEOS this was the `<Problem>` skeleton; for OpenFOAM it's the
> `0/ constant/ system/` case directory layout.

## RAG search tools (via the {{SLUG}}-rag MCP server)

Use these before writing:
- {{RAG_TOOL_NAMES}} — three retrieval tools backed by ChromaDB collections.

Validate every output with `mcp__{{SLUG}}-rag__{{LINT_MCP_TOOL}}` before finishing.
