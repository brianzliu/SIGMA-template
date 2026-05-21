---
name: plugin-maintainer
description: Use this when creating, reviewing, or extending this Claude Code plugin.
---

You are maintaining a Claude Code plugin.

Before changing plugin behavior:

1. Inspect `.claude-plugin/plugin.json` and the relevant component directory.
2. Keep plugin components at the repository root, not inside `.claude-plugin/`.
3. Prefer `skills/<name>/SKILL.md` for new reusable workflows.
4. Use `${CLAUDE_PLUGIN_ROOT}` when referring to bundled scripts or files.
5. Use `${CLAUDE_PLUGIN_DATA}` only for persistent generated state, caches, or dependencies.

When adding a component:

- Skills live in `skills/<skill-name>/SKILL.md`.
- Agents live in `agents/<agent-name>.md`.
- Hooks live in `hooks/hooks.json`.
- MCP server definitions live in `.mcp.json`.
- Executables that should be callable from Bash live in `bin/`.
- Utility scripts used by hooks, MCP servers, or skills live in `scripts/`.

After edits, validate by loading the plugin with:

```bash
claude --plugin-dir .
```

Inside Claude Code, run `/reload-plugins` after changes and try the skill as `/repo3-plugin:plugin-maintainer`.
