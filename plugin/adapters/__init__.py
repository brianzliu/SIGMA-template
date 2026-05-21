# Adapter modules house all simulator-specific behavior the template can't
# express as a yaml flag. Currently:
#
#   validator.py    — validate_text / validate_file / validate_directory
#   rag_indexer.py  — how documentation is chunked + indexed into ChromaDB
#
# The Stop hook and PostToolUse hook import validator; build_chromadb.py
# imports rag_indexer.
