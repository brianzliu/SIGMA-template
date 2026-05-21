#!/usr/bin/env python3
"""Build the three ChromaDB collections used by the {{SIMULATOR_NAME}} RAG MCP server.

Reads chunked documents from plugin/adapters/rag_indexer.py (iter_navigator,
iter_schema, iter_technical) and writes them into ChromaDB collections at
the path configured by {{ENV_PREFIX}}_VECTOR_DB_DIR (or rag.vector_db_dir
in configs/simulator.yaml).

Run this once after init_template.py and after writing rag_indexer.py.
Re-run whenever the simulator's source corpus changes.

Usage:
  python scripts/build_chromadb.py [--source <path-to-simulator-source>]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import chromadb
    from chromadb.utils import embedding_functions
except ImportError:
    sys.exit("chromadb required. Install with: pip install chromadb")

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required. Install with: pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "plugin"))
from adapters.rag_indexer import iter_navigator, iter_schema, iter_technical  # noqa: E402


def load_config() -> dict:
    with (REPO_ROOT / "configs" / "simulator.yaml").open() as f:
        return yaml.safe_load(f)


def main() -> None:
    cfg = load_config()
    rag = cfg["rag"]
    src_paths = cfg["source_paths"]

    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=src_paths["host_lib_dir"],
                    help="Path to the simulator source (docs + examples).")
    ap.add_argument("--vector-db-dir", default=rag["vector_db_dir"])
    args = ap.parse_args()

    source = Path(args.source).expanduser()
    db_dir = Path(args.vector_db_dir).expanduser()
    db_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(db_dir))
    # Default embedding: deterministic hash if no API key, OpenAI otherwise.
    embed_kind = rag.get("embedding", "hash")
    if embed_kind.startswith("openai:") and os.environ.get("OPENAI_API_KEY"):
        embed_fn = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.environ["OPENAI_API_KEY"],
            model_name=embed_kind.split(":", 1)[1],
        )
    else:
        embed_fn = embedding_functions.DefaultEmbeddingFunction()

    slug = cfg["simulator"]["slug"]
    schema_path = Path(cfg["linter"]["schema_path"]) if cfg["linter"].get("schema_path") else None

    sources = {
        f"{slug}_navigator": iter_navigator(source),
        f"{slug}_schema":    iter_schema(source, schema_path),
        f"{slug}_technical": iter_technical(source),
    }

    for name, docs in sources.items():
        coll = client.get_or_create_collection(name=name, embedding_function=embed_fn)
        ids, texts, metas = [], [], []
        for doc in docs:
            ids.append(doc["id"])
            texts.append(doc["text"])
            metas.append(doc["metadata"])
        if not ids:
            print(f"  {name}: no documents")
            continue
        coll.add(ids=ids, documents=texts, metadatas=metas)
        print(f"  {name}: indexed {len(ids)} documents")

    print(f"\nWrote ChromaDB to {db_dir}")
    print(f"Set {cfg.get('env_prefix', {}).get('override') or slug.upper()}_VECTOR_DB_DIR={db_dir} to use it.")


if __name__ == "__main__":
    main()
