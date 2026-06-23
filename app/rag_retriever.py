"""
RAG retriever for user-provided context documents (text or PDF).
Indexes any file uploaded by the user into ChromaDB.
No hardcoded source files — the user supplies all context.
"""

import os
from typing import Optional

CHROMA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "chroma_db",
)
COLLECTION_NAME = "user_context"


class RAGRetriever:
    """
    Wraps ChromaDB + OpenAI embeddings for user-uploaded context documents.
    Supports text (.txt) and PDF (.pdf) files.
    """

    def __init__(
        self,
        chroma_dir: str = CHROMA_DIR,
        embedding_provider: str = "openai",
        openai_api_key: Optional[str] = None,
    ):
        self.chroma_dir = chroma_dir
        self.embedding_provider = embedding_provider
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        self._vectorstore = None

    def _get_embeddings(self):
        if self.embedding_provider == "openai":
            from langchain_openai import OpenAIEmbeddings
            return OpenAIEmbeddings(
                model="text-embedding-3-small",
                api_key=self.openai_api_key,
            )
        raise ValueError(f"Unknown embedding provider: {self.embedding_provider}")

    # ── File parsing ──────────────────────────────────────────────────────────

    def _parse_text(self, file_path: str) -> list[str]:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
        chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 20]
        return chunks

    def _parse_pdf(self, file_path: str) -> list[str]:
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                raise ImportError("PDF support requires pypdf — run: pip install pypdf")

        reader = PdfReader(file_path)
        chunks = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            parts = [p.strip() for p in page_text.split("\n\n") if len(p.strip()) > 20]
            chunks.extend(parts)
        return chunks

    def read_full_text(self, file_path: str) -> str:
        """Return the entire document text (txt or pdf), joined by paragraph breaks.
        Used for context-only mode where the full doc is injected as the schema."""
        ext = os.path.splitext(file_path)[1].lower()
        chunks = self._parse_pdf(file_path) if ext == ".pdf" else self._parse_text(file_path)
        return "\n\n".join(chunks)

    # ── Indexing ─────────────────────────────────────────────────────────────

    def index_file(self, file_path: str, source_name: str = "context") -> str:
        """
        Index a text or PDF file into ChromaDB, replacing any previous index.
        Returns a status message. Never raises.
        """
        if not self.openai_api_key:
            return "RAG skipped — OpenAI API key not set."

        try:
            from langchain_chroma import Chroma
            from langchain_core.documents import Document

            ext = os.path.splitext(file_path)[1].lower()
            raw_chunks = self._parse_pdf(file_path) if ext == ".pdf" else self._parse_text(file_path)

            if not raw_chunks:
                return "RAG skipped — no readable content found in the uploaded file."

            documents = [
                Document(
                    page_content=chunk,
                    metadata={"source": source_name, "chunk_index": i},
                )
                for i, chunk in enumerate(raw_chunks)
            ]

            embeddings = self._get_embeddings()
            self._delete_collection()

            self._vectorstore = Chroma.from_documents(
                documents=documents,
                embedding=embeddings,
                collection_name=COLLECTION_NAME,
                persist_directory=self.chroma_dir,
            )
            return f"RAG index built: {len(documents)} chunks indexed from '{source_name}'."

        except Exception as e:
            self._vectorstore = None
            return f"RAG skipped — indexing failed: {e}"

    def load_existing(self) -> str:
        """
        Load an existing persisted ChromaDB index without re-indexing.
        Returns a status message. Never raises.
        """
        if not self.openai_api_key:
            return "RAG skipped — OpenAI API key not set."
        try:
            from langchain_chroma import Chroma
            embeddings = self._get_embeddings()
            self._vectorstore = Chroma(
                collection_name=COLLECTION_NAME,
                embedding_function=embeddings,
                persist_directory=self.chroma_dir,
            )
            count = self._vectorstore._collection.count()
            if count == 0:
                self._vectorstore = None
                return "No RAG context loaded — upload a context document to enable retrieval."
            return f"RAG context loaded from cache ({count} chunks)."
        except Exception as e:
            self._vectorstore = None
            return f"RAG unavailable — {e}"

    def clear(self):
        """Delete the persisted vector index and reset in-memory state."""
        self._delete_collection()
        self._vectorstore = None

    def _delete_collection(self):
        try:
            import chromadb
            client = chromadb.PersistentClient(path=self.chroma_dir)
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def retrieve(self, question: str, top_k: int = 5) -> list[dict]:
        if self._vectorstore is None:
            return []
        try:
            results = self._vectorstore.similarity_search_with_relevance_scores(question, k=top_k)
            return [
                {
                    "text": doc.page_content,
                    "score": round(score, 3),
                    "source": doc.metadata.get("source", "context"),
                }
                for doc, score in results
            ]
        except Exception:
            return []

    def retrieve_as_context(self, question: str, top_k: int = 5) -> str:
        """Return relevant context chunks as a formatted string for the LLM prompt."""
        chunks = self.retrieve(question, top_k=top_k)
        if not chunks:
            return ""
        lines = ["=== RELEVANT CONTEXT (from your uploaded document) ==="]
        for c in chunks:
            lines.append(c["text"])
            lines.append("")
        return "\n".join(lines)
