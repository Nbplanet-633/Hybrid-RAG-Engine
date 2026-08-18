"""Ask My Docs — a production-grade, domain-specific RAG system.

Public surface:

    from askmydocs import RAGPipeline, load_config

    pipeline = RAGPipeline.from_config(load_config())
    pipeline.ingest(["data/corpus"])
    answer = pipeline.answer("How long do I have to respond to a dispute?")
"""

from askmydocs.config import AppConfig, load_config
from askmydocs.models import Answer, Chunk, Citation, Document, RetrievedChunk

__version__ = "1.0.0"

__all__ = [
    "Answer",
    "AppConfig",
    "Chunk",
    "Citation",
    "Document",
    "RetrievedChunk",
    "load_config",
    "RAGPipeline",
    "__version__",
]


def __getattr__(name: str):  # pragma: no cover - thin lazy-import shim
    # RAGPipeline pulls in the retrieval/generation stack; import it lazily so that
    # `import askmydocs` stays cheap for callers that only need the data models.
    if name == "RAGPipeline":
        from askmydocs.pipeline import RAGPipeline

        return RAGPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
