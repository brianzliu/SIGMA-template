#!/usr/bin/env python3
"""Apply configs/simulator.yaml to the SIGMA template.

What this does, in order:
  1. Loads configs/simulator.yaml.
  2. Renames primer files:    plugin/PRIMER_*.md       → plugin/{SLUG}_PRIMER_*.md
  3. Renames the RAG skill:   plugin/skills/sim-rag/   → plugin/skills/{slug}-rag/
  4. Renames RAG MCP script:  plugin/scripts/rag_mcp.py → plugin/scripts/{slug}_rag_mcp.py
  5. Substitutes placeholders across the codebase:
       {{SIMULATOR_NAME}}, {{SLUG}}, {{SLUG_UPPER}}, {{TAGLINE}},
       {{CONFIG_EXT}}, {{CONFIG_LABEL}}, {{SCHEMA_PATH}}, {{VECTOR_DB_DIR}},
       {{CONTAINER_LIB_DIR}}, {{HOST_LIB_DIR}}, {{DOCKER_IMAGE}},
       {{COLLECTION_NAMES}}, {{RAG_TOOL_NAMES}}, {{ENV_PREFIX}}
  6. Regenerates plugin/.claude-plugin/plugin.json from the yaml.

Run this ONCE after editing configs/simulator.yaml. The script is idempotent
in the sense that re-running it with the same yaml is a no-op, but switching
yamls and re-running will rename things again — use a clean checkout.

Usage:
  python scripts/init_template.py [--dry-run] [--config configs/simulator.yaml]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Install with: pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files we substitute placeholders in. Keep this list explicit — we don't
# want to accidentally rewrite binary files or someone's notes.
SUBSTITUTION_TARGETS = [
    "plugin/README.md",
    "plugin/hooks/hooks.json",
    "plugin/hooks/verify_outputs.py",
    "plugin/hooks/verify_post_write.py",
    "plugin/scripts/README.md",
    "plugin/skills/sim-rag/SKILL.md",
    "plugin/PRIMER_absolute_min.md",
    "plugin/PRIMER_minimal.md",
    "plugin/PRIMER_minimal_vanilla.md",
    "src/runner/constants.py",
    "src/runner/cli.py",
    "src/runner/prompts/rag_instructions.txt",
    "src/runner/prompts/missing_rag_disclaimer.txt",
    "run/AGENTS.md",
    "run/Dockerfile",
    "README.md",
]


def load_config(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def build_substitutions(cfg: dict) -> dict[str, str]:
    sim = cfg["simulator"]
    fmt = cfg["config_format"]
    lint = cfg["linter"]
    rag = cfg["rag"]
    src = cfg["source_paths"]

    slug = sim["slug"]
    env_prefix = (cfg.get("env_prefix") or {}).get("override") or slug.upper()

    return {
        "{{SIMULATOR_NAME}}": sim["name"],
        "{{SLUG}}": slug,
        "{{SLUG_UPPER}}": slug.upper(),
        "{{ENV_PREFIX}}": env_prefix,
        "{{TAGLINE}}": sim["tagline"],
        "{{CONFIG_EXT}}": fmt["extensions"][0],
        "{{CONFIG_EXTS_JSON}}": json.dumps(fmt["extensions"]),
        "{{CONFIG_LABEL}}": fmt["label"],
        "{{SCHEMA_PATH}}": lint.get("schema_path") or "",
        "{{LINT_COMMAND}}": lint.get("command_template") or "",
        "{{LINT_MCP_TOOL}}": lint.get("mcp_tool_name", "validate_config"),
        "{{VECTOR_DB_DIR}}": rag["vector_db_dir"],
        "{{CONTAINER_LIB_DIR}}": src["container_lib_dir"],
        "{{HOST_LIB_DIR}}": src["host_lib_dir"],
        "{{DOCKER_IMAGE}}": src["docker_image"],
        "{{COLLECTION_NAMES}}": ",".join(c["name"] for c in rag["collections"]),
        "{{RAG_TOOL_NAMES}}": ",".join(c["mcp_tool"] for c in rag["collections"]),
    }


def substitute_in(path: Path, subs: dict[str, str], dry_run: bool) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    new = text
    for placeholder, value in subs.items():
        new = new.replace(placeholder, value)
    if new == text:
        return False
    if not dry_run:
        path.write_text(new)
    return True


def rename_files(cfg: dict, dry_run: bool) -> list[tuple[Path, Path]]:
    slug = cfg["simulator"]["slug"]
    renames: list[tuple[Path, Path]] = []
    # Primer files
    for src in (REPO_ROOT / "plugin").glob("PRIMER_*.md"):
        dst = src.parent / f"{slug.upper()}_PRIMER_{src.stem[len('PRIMER_'):]}.md"
        renames.append((src, dst))
    # Skill directory
    sim_rag = REPO_ROOT / "plugin" / "skills" / "sim-rag"
    if sim_rag.exists():
        renames.append((sim_rag, sim_rag.parent / f"{slug}-rag"))
    # RAG MCP script
    rag_script = REPO_ROOT / "plugin" / "scripts" / "rag_mcp.py"
    if rag_script.exists():
        renames.append((rag_script, rag_script.parent / f"{slug}_rag_mcp.py"))
    if not dry_run:
        for src, dst in renames:
            src.rename(dst)
    return renames


def regenerate_plugin_json(cfg: dict, dry_run: bool) -> None:
    sim = cfg["simulator"]
    rag = cfg["rag"]
    env_prefix = (cfg.get("env_prefix") or {}).get("override") or sim["slug"].upper()
    plugin_json = {
        "name": f"sigma-{sim['slug']}-plugin",
        "version": "0.1.0",
        "description": f"{sim['name']} authoring plugin: {sim['slug']}-rag MCP + self-verification Stop hook.",
        "author": {"name": "SIGMA"},
        "license": "MIT",
        "keywords": ["claude-code", "plugin", sim["slug"], "rag", "sigma"],
        "mcpServers": {
            f"{sim['slug']}-rag": {
                "command": "uv",
                "args": ["run", "--script", f"${{CLAUDE_PLUGIN_ROOT}}/scripts/{sim['slug']}_rag_mcp.py"],
                "env": {f"{env_prefix}_VECTOR_DB_DIR": rag["vector_db_dir"]},
            }
        },
    }
    out = REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json"
    if dry_run:
        print(f"[dry-run] would write {out}")
    else:
        out.write_text(json.dumps(plugin_json, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/simulator.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg_path = REPO_ROOT / args.config
    if not cfg_path.exists():
        sys.exit(f"config not found: {cfg_path}")
    cfg = load_config(cfg_path)
    subs = build_substitutions(cfg)

    print(f"Applying SIGMA template for {cfg['simulator']['name']!r} (slug={cfg['simulator']['slug']!r})")
    if args.dry_run:
        print("[DRY RUN] no files will be modified")

    renames = rename_files(cfg, args.dry_run)
    for src, dst in renames:
        print(f"  rename {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}")

    # Refresh substitution target paths to account for renames
    targets = list(SUBSTITUTION_TARGETS)
    slug = cfg["simulator"]["slug"]
    targets = [
        t.replace("plugin/skills/sim-rag/", f"plugin/skills/{slug}-rag/")
         .replace("plugin/PRIMER_", f"plugin/{slug.upper()}_PRIMER_")
        for t in targets
    ]

    changed = 0
    for rel in targets:
        if substitute_in(REPO_ROOT / rel, subs, args.dry_run):
            print(f"  subst {rel}")
            changed += 1

    regenerate_plugin_json(cfg, args.dry_run)
    print(f"\nDone. {changed} file(s) substituted, {len(renames)} renamed.")
    if not args.dry_run:
        print("Next: review the diff, build the ChromaDB indices with"
              " `python scripts/build_chromadb.py`, and implement"
              " plugin/adapters/validator.py + rag_indexer.py for your simulator.")


if __name__ == "__main__":
    main()
