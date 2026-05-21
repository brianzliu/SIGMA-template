# Adapting SIGMA to a new simulator

This guide walks through porting SIGMA to a simulator other than GEOS. The
GEOS → OpenFOAM port (which is what motivated this template) is used as the
running worked example.

## The four components, in order of difficulty

| Component | Difficulty | Why |
|---|---|---|
| Memory primer | Easiest | Just markdown. Open the three `PRIMER_*.md` files, rewrite the body for your simulator, keep the placeholder tokens. |
| RAG (ChromaDB) | Medium | Three collections. The hard part is deciding what to chunk and how, not the wiring. |
| Linter | Medium | The architecture is fixed; you implement two functions in `plugin/adapters/validator.py`. |
| Self-refinement verifier | Free | Falls out of the linter — the Stop hook already calls into `validator.py`. No additional work. |

## Step 0: edit the yaml

`configs/simulator.yaml` is the single source of truth. The `init_template.py`
script reads it and applies the values across the codebase (renaming files,
substituting `{{PLACEHOLDERS}}`, regenerating `plugin/.claude-plugin/plugin.json`).

Fields that matter most:

- `simulator.slug` — drives every env var prefix, the MCP server name, the
  skill directory name, and primer file names. Use a short lowercase
  identifier (`geos`, `openfoam`, `fluent`).
- `config_format.extensions` — the file extensions the linter will check.
- `config_format.output_is_directory` — true if the agent's output is a tree
  of files (OpenFOAM case dirs) rather than one file (GEOS XML deck).
- `linter.command_template` — if your simulator has a CLI lint tool with a
  schema file (like `xmllint --schema schema.xsd --noout file.xml`), put it
  here. If validation is pure-Python, leave it null and put the logic in
  `validator.validate_file()`.
- `rag.collections` — list of three collections. The convention is conceptual
  / authoritative / examples. For GEOS that was navigator / schema /
  technical; for OpenFOAM it became tutorials / cases / commands. Change the
  names but keep three of them; the MCP server expects exactly three.

## Step 1: primer

Open `plugin/PRIMER_minimal.md`. Most of the placeholders (`{{SIMULATOR_NAME}}`,
`{{CONFIG_LABEL}}`, etc.) will be filled by `init_template.py`. What you have
to write yourself is the "top-level structure" section — the canonical
skeleton a valid output file looks like. For GEOS this was the `<Problem>`
XML skeleton with `<Solvers>`, `<Mesh>`, etc.; for OpenFOAM it was the
`0/ constant/ system/` case directory layout.

Then write `PRIMER_minimal_vanilla.md` — the long version with worked
examples, boundary-condition cheatsheets, common errors. This is where you
encode the field-specific knowledge that prevents the model from
hallucinating. Length: ~400 lines for GEOS, ~250 for OpenFOAM.

Set `memory_primer.default_variant` in the yaml to pick which variant is
default at runtime.

## Step 2: RAG

You're populating three ChromaDB collections from your simulator's
documentation. The wiring is in `plugin/scripts/rag_mcp.py` (already
templated); you supply the indexing logic in `plugin/adapters/rag_indexer.py`.

The adapter exposes three generators — `iter_navigator`, `iter_schema`,
`iter_technical` — each yielding dicts of shape:

```python
{"id": "...", "text": "...", "metadata": {"source": "...", ...}}
```

For each generator, decide:

- **What is the source?** GEOS used `<source>/src/docs/**/*.rst` for navigator,
  one XSD file for schema, and `<source>/inputFiles/**/*.xml` for technical.
  OpenFOAM used FoamGPT tutorials, OpenFOAM tutorial case dirs, and
  CLI command help text.

- **What is the chunk size?** Per file is usually too big; per element /
  per section is usually right. The GEOS schema indexer chunks per
  `<xs:element>`. Look at how OpenFOAM's `openfoam_rag_mcp.py` chunks for a
  second worked example.

- **What metadata helps retrieval?** At minimum a `source` path. For examples,
  include `xml_reference` (or your equivalent) and `line_range` so the agent
  can read the actual file after seeing a snippet.

Once your `rag_indexer.py` is written, run `python scripts/build_chromadb.py`
to populate the three collections at `rag.vector_db_dir`. Re-run whenever
your source corpus changes.

## Step 3: linter

`plugin/adapters/validator.py` has three functions. You only need to
implement the ones that match your simulator:

```python
def validate_text(text: str, path: Path) -> list[ValidationIssue]:
    """Fast in-process check. Called by PostToolUse hook after each write.
    Default: parses as XML. Override for your simulator's grammar."""

def validate_file(path: Path, schema: Path | None) -> list[ValidationIssue]:
    """Heavier check, may shell out. Called by Stop hook."""

def validate_directory(root: Path) -> list[ValidationIssue]:
    """Only needed if config_format.output_is_directory is true."""
```

Two patterns from the existing adaptations:

**GEOS-style (subprocess + schema):**
```python
def validate_file(path: Path, schema: Path | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if schema and shutil.which("xmllint"):
        res = subprocess.run(
            ["xmllint", "--schema", str(schema), "--noout", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode != 0:
            for line in res.stderr.splitlines():
                if line.strip():
                    issues.append(ValidationIssue("schema_error", line.strip(), file=path))
    return issues
```

**OpenFOAM-style (pure Python structural check):**
```python
def validate_text(text: str, path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for left, right, label in [("{", "}", "braces"), ("(", ")", "parens")]:
        if not _balanced_pairs(text, left, right):
            issues.append(ValidationIssue("unbalanced", f"unbalanced {label}", file=path))
    if "FoamFile" not in text and path.suffix not in {".sh", ".py"}:
        issues.append(ValidationIssue("missing_header", "missing FoamFile header", file=path))
    return issues
```

## Step 4: self-refinement (free)

You already wrote it in step 3. The Stop hook (`plugin/hooks/verify_outputs.py`)
calls `validate_file` (or `validate_directory`) and re-enters the agent
with the issues as feedback. The counter at `<inputs-parent>/.verify_retry_count`
caps retries at `{ENV_PREFIX}_HOOK_MAX_RETRIES` (default 2).

If you want different retry behavior — e.g. infinite retries with a longer
delay, or different per-category limits — edit `_bump_counter` and the
`max_retries` branch in `verify_outputs.py::main`.

## Sanity check

```bash
# Lint that init_template did its job
grep -rn "GEOS\|geos" plugin/ src/ --include="*.py" --include="*.md" --include="*.json"
# (should be empty after init_template.py runs against a non-GEOS config)

# Smoke-test the plugin
cd plugin && claude --plugin-dir .
> /reload-plugins
> /sigma-<your-slug>:plugin-maintainer

# Trigger the Stop hook against a deliberately bad output
mkdir -p /tmp/workspace/inputs
echo "<broken xml" > /tmp/workspace/inputs/bad.xml   # adapt to your config_format
CLAUDE_PROJECT_DIR=/tmp/workspace echo '{}' | python plugin/hooks/verify_outputs.py
# Expect: {"decision":"block","reason":"... validation failed ..."}
```

## Two existing adaptations as reference

- [`examples/geos/simulator.yaml`](../examples/geos/simulator.yaml) — XML
  deck, XSD schema, xmllint subprocess
- [`examples/openfoam/simulator.yaml`](../examples/openfoam/simulator.yaml) —
  case directory, pure-Python structural check, no schema

Read [github.com/matt-seb-ho/repo3](https://github.com/matt-seb-ho/repo3)
for the original GEOS implementation and
[github.com/brianzliu/repo3_openfoam](https://github.com/brianzliu/repo3_openfoam)
for the OpenFOAM adaptation if you want concrete diffs.
