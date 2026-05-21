# /// script
# dependencies = [
#   "mcp>=1.0.0,<2",
# ]
# ///
"""MCP server exposing a single GEOS XML schema-validation tool.

The tool is a thin wrapper over ``xmllint --schema``. Same validator the
``GEOS_HOOK_XMLLINT`` Stop-hook variant uses, but exposed as a tool the
agent can call mid-task to pre-validate a draft instead of waiting for
the end-of-turn hook.

Tool name (with the ``mcp__xmllint__`` prefix Claude Code applies):

    mcp__xmllint__validate_geos_xml(xml_path: str) -> str

The argument can be either an absolute path inside the container
(e.g. ``/workspace/inputs/foo.xml``) or relative to ``/workspace/`` /
``/workspace/inputs/``. The schema path is configurable via
``XMLLINT_SCHEMA_PATH`` (defaults to
``/geos_lib/src/coreComponents/schema/schema.xsd``).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DEFAULT_SCHEMA_PATH = Path(
    os.environ.get(
        "XMLLINT_SCHEMA_PATH",
        "/geos_lib/src/coreComponents/schema/schema.xsd",
    )
)
DEFAULT_WORKSPACE = Path(os.environ.get("XMLLINT_WORKSPACE_DIR", "/workspace"))
INPUTS_DIR = DEFAULT_WORKSPACE / "inputs"

mcp = FastMCP("xmllint")


def _resolve(xml_path: str) -> Path:
    p = Path(xml_path)
    if p.is_absolute():
        return p
    # try ./, /workspace/, /workspace/inputs/
    for candidate in (Path.cwd() / p, DEFAULT_WORKSPACE / p, INPUTS_DIR / p):
        if candidate.exists():
            return candidate
    return p  # let xmllint emit the not-found error


@mcp.tool()
def validate_geos_xml(xml_path: str) -> str:
    """Validate a GEOS XML file against the GEOS XSD schema.

    Use this BEFORE finishing your turn on every XML you produced. It
    catches the most common error class on this task: hallucinated
    element names, wrong attribute spellings, missing required
    attributes. The errors xmllint reports include the *expected*
    element/attribute names — do not guess again, read the suggestion.

    Args:
        xml_path: Path to the XML file. Absolute (``/workspace/inputs/foo.xml``)
            or relative to the workspace (``inputs/foo.xml`` / ``foo.xml``).

    Returns:
        Either ``"<file>: validates"`` on success, or a multi-line block
        listing each schema error. Schema is resolved from the
        ``XMLLINT_SCHEMA_PATH`` env var or its container default.
    """
    if shutil.which("xmllint") is None:
        return (
            "ERROR: xmllint binary not present in this container. "
            "This validator requires the GEOS eval image with libxml2-utils. "
            "Validate by hand if possible."
        )
    if not DEFAULT_SCHEMA_PATH.exists():
        return (
            f"ERROR: schema not found at {DEFAULT_SCHEMA_PATH}. Set "
            f"XMLLINT_SCHEMA_PATH or fix the host mount."
        )
    target = _resolve(xml_path)
    if not target.exists():
        return f"ERROR: file not found: {target} (resolved from {xml_path!r})"
    try:
        res = subprocess.run(
            ["xmllint", "--schema", str(DEFAULT_SCHEMA_PATH), "--noout", str(target)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: xmllint timed out after 30s on {target}"
    if res.returncode == 0:
        return f"{target}: validates"
    # Collapse the multi-line stderr; drop the trailing 'fails to validate'
    err_lines = []
    for line in res.stderr.splitlines():
        line = line.strip()
        if not line: continue
        if line.endswith("fails to validate"): continue
        err_lines.append(line)
    body = "\n".join(err_lines) if err_lines else res.stderr.strip()
    return f"{target}: FAILS schema validation (xmllint exit={res.returncode})\n{body}"


if __name__ == "__main__":
    mcp.run()
