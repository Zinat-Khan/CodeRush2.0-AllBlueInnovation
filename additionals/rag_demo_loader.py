"""
AE-03 Additionals: Standalone RAG Document Loader.

Ingests sample documents into the Supabase pgvector / Chroma vector store
for multi-agent context retrieval testing.
"""

import sys
import os
import argparse
import logging

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.rag.pipeline import RAGPipeline
from backend.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("rag_demo_loader")


def ingest_file(file_path: str, workspace_id: str = "default") -> None:
    """Ingest a file into the RAG vector store."""
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return

    logger.info(f"Starting ingestion for '{file_path}' into workspace '{workspace_id}'...")
    settings = get_settings()
    pipeline = RAGPipeline(settings=settings)

    num_chunks = pipeline.ingest_document(file_path, workspace_id=workspace_id)
    logger.info(f"✓ Ingestion complete. Stored {num_chunks} vector chunks in store.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AE-03 RAG Document Loader")
    parser.add_argument("--file", type=str, required=True, help="Path to document (.pdf, .txt, .csv, .md)")
    parser.add_argument("--workspace", type=str, default="default", help="Workspace ID tag")
    args = parser.parse_args()

    ingest_file(args.file, args.workspace)
