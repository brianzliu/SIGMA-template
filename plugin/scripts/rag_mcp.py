# /// script
# dependencies = [
#   "chromadb>=1.0.0,<2",
#   "mcp>=1.0.0,<2",
#   "openai>=1.0.0,<3",
#   "python-dotenv>=1.0.0,<2",
# ]
# ///
"""MCP server exposing {{SIMULATOR_NAME}} ChromaDB RAG search tools.

Collection names and env var prefix are templated from
configs/simulator.yaml at init_template.py time. The three search_*
functions below correspond to rag.collections[0..2] in that yaml.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
ENV_PREFIX = "{{ENV_PREFIX}}"
DEFAULT_VECTOR_DB_DIR = Path("{{VECTOR_DB_DIR}}")
# Three collections, templated from rag.collections in simulator.yaml.
# If you change the count or names, also edit search_* functions below.
COLLECTION_NAVIGATOR = "{{SLUG}}_navigator"
COLLECTION_TECHNICAL = "{{SLUG}}_technical"
COLLECTION_SCHEMA = "{{SLUG}}_schema"
DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
DEFAULT_OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


load_dotenv(PLUGIN_ROOT / ".env", override=False)
load_dotenv(Path.cwd() / ".env", override=False)


def _vector_db_dir() -> Path:
    explicit = os.environ.get(f"{ENV_PREFIX}_VECTOR_DB_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    data_dir = os.environ.get(f"{ENV_PREFIX}_DATA_DIR")
    if data_dir:
        return (Path(data_dir).expanduser() / "vector_db").resolve()
    return DEFAULT_VECTOR_DB_DIR


def _load_env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _normalize_path(path: str | Path) -> str:
    return str(Path(path)).replace("\\", "/").lower()


_XML_VARIANT_SUFFIXES = (
    "_base_iterative",
    "_base_direct",
    "_iterative_base",
    "_direct_base",
    "_iterative",
    "_direct",
    "_benchmark",
    "_base",
)
_GENERIC_XML_STEMS = {"base", "benchmark", "input", "inputs", "problem", "model"}


def _xml_variant_keys(path: str | Path) -> frozenset[str]:
    candidate = Path(path)
    if candidate.suffix.lower() != ".xml":
        return frozenset()

    keys: set[str] = set()
    pending = [candidate.stem.lower()]
    while pending:
        stem = pending.pop()
        if stem in keys:
            continue
        keys.add(stem)
        for suffix in _XML_VARIANT_SUFFIXES:
            if stem.endswith(suffix):
                stripped = stem[: -len(suffix)]
                if stripped and stripped not in keys:
                    pending.append(stripped)

    return frozenset(
        key for key in keys if len(key) >= 10 and key not in _GENERIC_XML_STEMS
    )


@dataclass(frozen=True)
class ReferenceAccessPolicy:
    blocked_xml_filenames: frozenset[str]
    blocked_xml_variant_keys: frozenset[str]
    blocked_rst_paths: frozenset[str]

    @classmethod
    def from_environment(cls) -> "ReferenceAccessPolicy":
        xml_filenames = frozenset(
            item.lower()
            for item in _load_env_list("EXCLUDED_GT_XML_FILENAMES")
            if item.lower().endswith(".xml")
        )
        variant_keys: set[str] = set()
        for filename in xml_filenames:
            variant_keys.update(_xml_variant_keys(filename))
        return cls(
            blocked_xml_filenames=xml_filenames,
            blocked_xml_variant_keys=frozenset(variant_keys),
            blocked_rst_paths=frozenset(
                _normalize_path(item)
                for item in _load_env_list("EXCLUDED_RST_PATHS")
                if item.lower().endswith(".rst")
            ),
        )

    def is_blocked_xml_path(self, path: str | Path) -> bool:
        if not self.blocked_xml_filenames:
            return False
        candidate = Path(path)
        if candidate.suffix.lower() != ".xml":
            return False
        if candidate.name.lower() in self.blocked_xml_filenames:
            return True
        return bool(_xml_variant_keys(candidate) & self.blocked_xml_variant_keys)

    def is_blocked_rst_path(self, path: str | Path) -> bool:
        if not self.blocked_rst_paths:
            return False
        normalized = _normalize_path(path)
        return any(
            normalized == blocked or normalized.endswith(f"/{blocked}")
            for blocked in self.blocked_rst_paths
        )


class ChromaSearchBackend:
    def __init__(self) -> None:
        self.vector_db_dir = _vector_db_dir()
        self.client = chromadb.PersistentClient(path=str(self.vector_db_dir))
        self.embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            api_base=os.environ.get(
                "OPENROUTER_API_BASE",
                os.environ.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_API_BASE),
            ),
            model_name=os.environ.get(f"{ENV_PREFIX}_EMBEDDING_MODEL_NAME", DEFAULT_EMBEDDING_MODEL),
        )
        self._collections: dict[str, Any] = {}

    def get_collection(self, name: str) -> Any:
        if name not in self._collections:
            self._collections[name] = self.client.get_collection(
                name=name,
                embedding_function=self.embedding_fn,
            )
        return self._collections[name]


mcp = FastMCP("{{SLUG}}-rag")
_backend: ChromaSearchBackend | None = None
_policy: ReferenceAccessPolicy | None = None


def _get_backend() -> ChromaSearchBackend:
    global _backend
    if _backend is None:
        _backend = ChromaSearchBackend()
    return _backend


def _get_policy() -> ReferenceAccessPolicy:
    global _policy
    if _policy is None:
        _policy = ReferenceAccessPolicy.from_environment()
    return _policy


def _bounded_n_results(value: int, *, default: int, maximum: int = 20) -> int:
    try:
        n_results = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(n_results, maximum))


@mcp.tool()
def search_navigator(query: str, n_results: int = 5) -> dict[str, Any]:
    """Search {{SIMULATOR_NAME}} conceptual / navigator collection."""
    n_results = _bounded_n_results(n_results, default=5)
    collection = _get_backend().get_collection(COLLECTION_NAVIGATOR)
    results = collection.query(query_texts=[query], n_results=n_results)
    policy = _get_policy()

    formatted: list[dict[str, Any]] = []
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    for index, doc in enumerate(documents):
        meta = metadatas[index]
        source_path = meta.get("source_path", "")
        if policy.is_blocked_rst_path(source_path):
            continue
        formatted.append(
            {
                "title": meta.get("title", "No Title"),
                "breadcrumbs": meta.get("breadcrumbs", ""),
                "type": meta.get("chunk_type", "unknown"),
                "source": source_path,
                "preview": doc[:200] + "..." if len(doc) > 200 else doc,
            }
        )

    return {
        "query": query,
        "results": formatted,
        "hint": "Use read_file with path and optional line/marker params for full source content.",
    }


@mcp.tool()
def search_technical(query: str, n_results: int = 5) -> dict[str, Any]:
    """Search {{SIMULATOR_NAME}} technical / examples collection."""
    n_results = _bounded_n_results(n_results, default=5)
    collection = _get_backend().get_collection(COLLECTION_TECHNICAL)
    results = collection.query(query_texts=[query], n_results=n_results)
    policy = _get_policy()

    formatted: list[dict[str, Any]] = []
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    for index, doc in enumerate(documents):
        meta = metadatas[index]
        xml_ref = meta.get("xml_reference") or ""
        source_path = meta.get("source_path", "")
        if policy.is_blocked_xml_path(xml_ref):
            continue
        if policy.is_blocked_xml_path(source_path):
            continue
        if policy.is_blocked_rst_path(source_path):
            continue
        formatted.append(
            {
                "title": meta.get("title", "No Title"),
                "xml_reference": xml_ref,
                "line_range": meta.get("line_range", ""),
                "breadcrumbs": meta.get("breadcrumbs", ""),
                "source_path": source_path,
                "shadow_text": doc[:300] + "..." if len(doc) > 300 else doc,
            }
        )

    return {
        "query": query,
        "results": formatted,
        "hint": "Use read_file with path and start_line/end_line or markers to read actual XML.",
    }


@mcp.tool()
def search_schema(query: str, n_results: int = 3) -> dict[str, Any]:
    """Search {{SIMULATOR_NAME}} authoritative schema collection."""
    n_results = _bounded_n_results(n_results, default=3)
    collection = _get_backend().get_collection(COLLECTION_SCHEMA)
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    formatted: list[dict[str, Any]] = []
    for doc, meta, dist in zip(
        results.get("documents", [[]])[0],
        results.get("metadatas", [[]])[0],
        results.get("distances", [[]])[0],
    ):
        formatted.append(
            {
                "element": meta["element_name"],
                "title": meta["title"],
                "attribute_count": meta["attribute_count"],
                "spec": doc,
                "relevance": round(1 - dist, 4),
            }
        )

    return {
        "query": query,
        "results": formatted,
        "hint": (
            "The spec field contains attributes with types, defaults, and descriptions. "
            "Use this to write the XML element correctly."
        ),
    }


def _smoke() -> int:
    vector_db_dir = _vector_db_dir()
    client = chromadb.PersistentClient(path=str(vector_db_dir))
    names = sorted(collection.name for collection in client.list_collections())
    print(f"vector_db_dir={vector_db_dir}")
    print("collections=" + ",".join(names))
    missing = {
        COLLECTION_NAVIGATOR,
        COLLECTION_TECHNICAL,
        COLLECTION_SCHEMA,
    } - set(names)
    if missing:
        print("missing=" + ",".join(sorted(missing)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        raise SystemExit(_smoke())
    mcp.run()
