"""Streamlit demo UI: upload your documents, then ask questions about them.

    pip install 'ask-my-docs[ui]'
    streamlit run ui/streamlit_app.py

Questions are answered from the upload library (see ``askmydocs.library``), which
is indexed separately from the evaluated corpus, so nothing uploaded here can move
the evaluation numbers. The library starts empty; "Try with sample documents"
loads the bundled demo corpus into it for anyone without a document to hand.

Deliberately shows the machinery rather than hiding it: every answer displays its
citations with the supporting quote, and the retrieval trace shows which leg
(dense, BM25, or both) surfaced each passage and how the scores moved through
fusion and reranking. An abstention is rendered as a first-class outcome with its
reason, not as an error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run ui/streamlit_app.py` from the repo root without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from askmydocs.config import load_config  # noqa: E402
from askmydocs.ingest.loaders import SUPPORTED_SUFFIXES  # noqa: E402
from askmydocs.library import DocumentLibrary, UploadError  # noqa: E402

st.set_page_config(page_title="Ask My Docs", page_icon="📄", layout="wide")

ABSTAIN_EXPLANATIONS = {
    "no_results": "Nothing in the selected documents matched this question at all.",
    "low_relevance": "The best passage scored below the relevance floor.",
    "question_not_covered": "A key term in the question does not appear in your documents.",
    "model_refusal": "The generator judged the retrieved passages insufficient.",
    "no_citations": "The generated answer carried no resolvable source citation.",
    "low_grounding": "The answer's content was not supported by the passages it cited.",
    "empty_response": "The generator returned nothing.",
}
ALL_DOCUMENTS = "__all__"


@st.cache_resource(show_spinner="Loading your documents...")
def get_library(profile: str) -> DocumentLibrary:
    return DocumentLibrary(load_config(profile=profile))


def _size(num_bytes: int) -> str:
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _chunks(count: int) -> str:
    return f"{count} chunk" if count == 1 else f"{count} chunks"


def _add_files(library: DocumentLibrary, files: list[tuple[str, bytes]]) -> None:
    """Add files, reporting each outcome. One bad file does not stop the rest."""
    for name, data in files:
        try:
            summary = library.add(name, data)
        except UploadError as exc:
            st.error(str(exc))
        else:
            st.success(f"Added **{summary.filename}** ({_chunks(summary.chunks)}).")


with st.sidebar:
    st.title("Ask My Docs")
    st.caption("Upload documents, then ask questions. Every answer cites its source.")

    profile = st.selectbox(
        "Profile",
        ["offline", "full"],
        help=(
            "offline: hashed embeddings, lexical rerank, extractive answers. Free, no API "
            "key. full: sentence-transformers, cross-encoder, and Claude for written answers."
        ),
    )

    try:
        library = get_library(profile)
    except Exception as exc:  # noqa: BLE001 - surface the cause in the UI
        st.error(
            f"Could not initialise the **{profile}** profile.\n\n`{type(exc).__name__}: {exc}`"
        )
        st.stop()

    st.divider()
    st.subheader("Your documents")

    with st.form("upload", clear_on_submit=True, border=False):
        uploads = st.file_uploader(
            "Upload documents",
            type=sorted(suffix.lstrip(".") for suffix in SUPPORTED_SUFFIXES),
            accept_multiple_files=True,
            help=f"Up to {library.max_bytes / (1024 * 1024):g} MB per file.",
        )
        submitted = st.form_submit_button("Add to library", type="primary")
    if submitted and uploads:
        with st.spinner("Reading and indexing..."):
            _add_files(library, [(upload.name, upload.getvalue()) for upload in uploads])

    documents = library.documents()
    for document in documents:
        name_col, delete_col = st.columns([5, 1], vertical_alignment="center")
        name_col.markdown(
            f"**{document.filename}**  \n"
            f"<small>{_chunks(document.chunks)} · {_size(document.size_bytes)}</small>",
            unsafe_allow_html=True,
        )
        if delete_col.button("🗑", key=f"delete-{document.doc_id}", help="Remove"):
            library.delete(document.doc_id)
            st.rerun()

    if not documents:
        st.caption("No documents yet.")
        corpus = Path(library.pipeline.config.corpus_dir)
        if corpus.is_dir() and st.button("Try with sample documents"):
            with st.spinner("Loading the sample documents..."):
                _add_files(
                    library,
                    [
                        (path.name, path.read_bytes())
                        for path in sorted(corpus.iterdir())
                        if path.suffix.lower() in SUPPORTED_SUFFIXES
                    ],
                )
            st.rerun()

    st.divider()
    stats = library.pipeline.stats()
    top_n = st.slider("Passages in context", 1, 10, library.pipeline.config.rerank.top_n)
    if stats["generator"].startswith("extractive"):
        st.info(
            "**Quote mode.** Answers are the most relevant sentences from your documents, "
            "with citations. Switch to the **full** profile with an Anthropic API key for "
            "written answers.",
            icon="💬",
        )
    with st.expander("Engine details"):
        citations = library.pipeline.config.citations
        st.caption(
            f"**Embedder** `{stats['embedder']}`  \n"
            f"**Vector store** `{stats['vector_store']}`  \n"
            f"**Reranker** `{stats['reranker']}`  \n"
            f"**Generator** `{stats['generator']}`  \n"
            f"**Prompt** `{stats['prompt']}` (`{stats['prompt_hash']}`)  \n"
            f"**Abstention gates** relevance ≥ {citations.min_top_score}, "
            f"question coverage ≥ {citations.min_question_coverage}, "
            f"grounding ≥ {citations.min_grounding}"
        )

st.header("Ask your documents")

if not documents:
    st.info(
        "Upload a PDF, Markdown, text, or HTML file in the sidebar to begin, or click "
        "**Try with sample documents**."
    )
    st.stop()

by_id = {document.doc_id: document for document in documents}
scope = st.selectbox(
    "Search in",
    [ALL_DOCUMENTS, *by_id],
    format_func=lambda key: (
        f"All documents ({len(documents)})" if key == ALL_DOCUMENTS else by_id[key].filename
    ),
)

question = st.text_input(
    "Question",
    placeholder="Ask anything about your documents...",
    label_visibility="collapsed",
)

if not question:
    st.stop()

with st.spinner("Retrieving and generating..."):
    answer = library.ask(
        question,
        doc_ids=None if scope == ALL_DOCUMENTS else [scope],
        top_n=top_n,
    )

if answer.abstained:
    st.warning(f"**No answer returned.**\n\n{answer.text}")
    st.caption(
        f"Reason: `{answer.abstain_reason}` — "
        f"{ABSTAIN_EXPLANATIONS.get(answer.abstain_reason, 'See the abstention gates.')}"
    )
    st.caption(
        "This is the intended behaviour when the evidence is insufficient: the system "
        "refuses rather than guessing."
    )
else:
    st.success(answer.text)

    a, b, c, d = st.columns(4)
    a.metric("Confidence", f"{answer.confidence:.2f}")
    b.metric("Grounding", f"{answer.grounding_score:.2f}")
    c.metric("Citations", len(answer.citations))
    d.metric("Latency", f"{answer.latency_ms:.0f} ms")

    st.subheader("Citations")
    for citation in answer.citations:
        with st.container(border=True):
            st.markdown(f"**`{citation.marker}`** · `{Path(citation.source).name}`")
            if citation.section:
                st.caption(citation.section)
            st.markdown(f"> {citation.quote}")

with st.expander(f"Retrieval trace ({len(answer.retrieved)} passages)", expanded=False):
    st.caption(
        "`hybrid` means both the dense and BM25 legs surfaced the passage — the "
        "strongest signal available before reranking."
    )
    st.dataframe(
        [
            {
                "rank": index,
                "score": round(item.score, 4),
                "dense rank": item.dense_rank,
                "BM25 rank": item.lexical_rank,
                "leg": item.retriever,
                "source": Path(item.chunk.source).name,
                "section": item.chunk.section,
                "tokens": item.chunk.token_count,
            }
            for index, item in enumerate(answer.retrieved, start=1)
        ],
        use_container_width=True,
        hide_index=True,
    )

st.caption(
    f"Answered by `{answer.model}` using prompt `{answer.prompt_name}.{answer.prompt_version}` · "
    f"{answer.usage.input_tokens} in / {answer.usage.output_tokens} out tokens"
)
