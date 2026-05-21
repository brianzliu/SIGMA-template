"""Simulator-specific validation adapter.

The Stop hook and PostToolUse hook call into this module to check whether
the agent's output is well-formed. There are two extension points:

  validate_text(text, path)   — fast, in-process check (parse / structure).
                                Used by the PostToolUse hook so the agent
                                gets feedback within seconds of a bad write.

  validate_file(path, schema) — heavier check that may shell out
                                (e.g. `xmllint --schema`, `foamDictionary`).
                                Used by the Stop hook when
                                linter.command_template is null in
                                configs/simulator.yaml.

Each returns a list[ValidationIssue]. An empty list means "valid".

The default implementations below are written for GEOS XML. To adapt to a
new simulator, either:
  (a) edit this file in place to call your simulator's parser, or
  (b) leave config_format.parser set to "xml" / "json" / "yaml" in
      simulator.yaml and skip writing this file — the built-in parsers
      handle those cases.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ValidationIssue:
    category: str            # e.g. "parse_error", "schema_error", "unbalanced"
    message: str             # one-line description shown to the agent
    file: Path | None = None
    hint: str | None = None  # optional fix-it hint appended to message


def validate_text(text: str, path: Path) -> list[ValidationIssue]:
    """Fast in-process check. Called once per Write/Edit/MultiEdit.

    Default: parse as XML. Override for OpenFOAM dictionaries, JSON, YAML,
    or anything else.
    """
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        return [ValidationIssue(
            category="parse_error",
            message=f"XML parse error in {path.name}: {exc}",
            file=path,
            hint="Open the file and fix the syntax before continuing.",
        )]
    return []


def validate_file(path: Path, schema_path: Path | None = None) -> list[ValidationIssue]:
    """Heavier check, may shell out. Called by the Stop hook.

    Default: re-parse as XML (no schema check unless schema_path is set and
    xmllint is available). Override to call simulator-specific tools.
    """
    try:
        text = path.read_text(errors="ignore")
    except OSError as exc:
        return [ValidationIssue("read_error", f"could not read {path}: {exc}", file=path)]
    return validate_text(text, path)


def validate_directory(root: Path) -> list[ValidationIssue]:
    """Optional: validate a directory of files (e.g. an OpenFOAM case dir).

    Only called when config_format.output_is_directory is true. Default
    implementation walks the tree and runs validate_file on each file.
    """
    issues: list[ValidationIssue] = []
    if not root.exists():
        return [ValidationIssue("missing", f"{root} does not exist", file=root)]
    for f in sorted(root.rglob("*")):
        if f.is_file():
            issues.extend(validate_file(f))
    return issues
