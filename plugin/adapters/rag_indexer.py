"""Simulator-specific document chunking + indexing for the RAG ChromaDB.

`scripts/build_chromadb.py` calls into this module to populate the three
collections defined in configs/simulator.yaml (default: navigator / schema /
technical). The default implementation below indexes:

  navigator  — markdown files anywhere under <source>/docs/
  schema     — a single XSD file (path from configs/simulator.yaml linter.schema_path)
  technical  — every .xml file under <source>/inputFiles/

To adapt to another simulator, edit the three iter_* functions to walk
your simulator's documentation, schema, and examples.

Each iter_* yields dicts of shape:
  {"id": str, "text": str, "metadata": {"source": str, "line_start": int, ...}}
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator


def iter_navigator(source_root: Path) -> Iterator[dict]:
    """Yield documents for the 'navigator' (conceptual) collection."""
    for md in sorted(source_root.rglob("docs/**/*.md")):
        try:
            text = md.read_text(errors="ignore")
        except OSError:
            continue
        yield {
            "id": str(md.relative_to(source_root)),
            "text": text,
            "metadata": {"source": str(md.relative_to(source_root)), "type": "doc"},
        }


def iter_schema(source_root: Path, schema_path: Path | None) -> Iterator[dict]:
    """Yield documents for the 'schema' (authoritative) collection.

    Default: chunk the XSD by xsd:element / xsd:complexType. Override for
    OpenAPI specs, JSON schemas, etc.
    """
    if schema_path is None or not schema_path.exists():
        return
    try:
        text = schema_path.read_text(errors="ignore")
    except OSError:
        return
    # Naive chunk: one chunk per top-level element block. Real GEOS schema
    # indexing in repo_3 is more careful — see scripts/build_chromadb.py.
    chunk: list[str] = []
    for line in text.splitlines():
        chunk.append(line)
        if line.strip().startswith("</xs:element>"):
            yield {
                "id": f"schema:{len(chunk)}",
                "text": "\n".join(chunk),
                "metadata": {"source": str(schema_path), "type": "schema"},
            }
            chunk = []


def iter_technical(source_root: Path) -> Iterator[dict]:
    """Yield documents for the 'technical' (examples) collection."""
    for xml in sorted(source_root.rglob("inputFiles/**/*.xml")):
        try:
            text = xml.read_text(errors="ignore")
        except OSError:
            continue
        yield {
            "id": str(xml.relative_to(source_root)),
            "text": text,
            "metadata": {
                "source": str(xml.relative_to(source_root)),
                "type": "example",
                "xml_reference": str(xml.relative_to(source_root)),
            },
        }
