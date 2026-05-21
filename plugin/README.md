# GEOS-plugin

Starter Claude Code plugin scaffold.

## Structure

```text
.
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── plugin-maintainer/
│       └── SKILL.md
├── scripts/
├── bin/
├── CHANGELOG.md
└── README.md
```

Only `.claude-plugin/plugin.json` belongs inside `.claude-plugin/`. Plugin components such as `skills/`, `agents/`, `hooks/`, `scripts/`, and `bin/` belong at the plugin root.

## Local Testing

From this directory:

```bash
claude --plugin-dir .
```

Inside Claude Code:

```text
/reload-plugins
/GEOS-plugin:plugin-maintainer
```

## Adding Components

- Add reusable workflows under `skills/<name>/SKILL.md`.
- Add subagents under `agents/<name>.md`.
- Add lifecycle hooks under `hooks/hooks.json`.
- Add MCP servers in `.mcp.json`.
- Add helper scripts under `scripts/`.
- Add executables that should be available on the Bash `PATH` under `bin/`.

Use `${CLAUDE_PLUGIN_ROOT}` for files shipped with the plugin and `${CLAUDE_PLUGIN_DATA}` for persistent generated state.
