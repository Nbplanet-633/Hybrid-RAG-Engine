"""Streamlit demo UI.

    pip install 'ask-my-docs[ui]'
    streamlit run ui/streamlit_app.py

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
from askmydocs.pipeline import RAGPipeline  # noqa: E402

st.set_page_config(page_title="Ask My Docs", page_icon="📄", layout="wide")

ABSTAIN_EXPLANATIONS = {
    "no_results": "Nothing in the index matched this question at all.",
    "low_relevance": "The best passage scored below the relevance floor.",
    "question_not_covered": "A key term in the question does not appear anywhere in the corpus.",
    "model_refusal": "The generator judged the retrieved passages insufficient.",
    "no_citations": "The generated answer carried no resolvable source citation.",
    "low_grounding": "The answer's content was not supported by the passages it cited.",
    "empty_response": "The generator returned nothing.",
}


@st.cache_resource(show_spinner="Loading index and models...")
def get_pipeline(profile: str) -> RAGPipeline:
    return RAGPipeline.from_config(load_config(profile=profile))


with st.sidebar:
    st.title("Ask My Docs")
    st.caption("Hybrid-retrieval RAG with enforced citations.")

    profile = st.selectbox(
        "Profile",
        ["offline", "full"],
        help=(
            "offline: hashed embeddings, lexical rerank, extractive generator — "
            "deterministic, no API key. full: sentence-transformers, cross-encoder, Claude."
        ),
    )

    try:
        pipeline = get_pipeline(profile)
    except Exception as exc:  # noqa: BLE001 - surface the cause in the UI
        st.error(
            f"Could not initialise the **{profile}** profile.\n\n`{type(exc).__name__}: {exc}`"
        )
        st.stop()

    stats = pipeline.stats()
    st.divider()
    st.subheader("Index")
    left, right = st.columns(2)
    left.metric("Documents", stats["documents"])
    right.metric("Chunks", stats["chunks"])

    st.caption(
        f"**Embedder** `{stats['embedder']}`  \n"
        f"**Vector store** `{stats['vector_store']}`  \n"
        f"**Reranker** `{stats['reranker']}`  \n"
        f"**Generator** `{stats['generator']}`  \n"
        f"**Prompt** `{stats['prompt']}` (`{stats['prompt_hash']}`)"
    )

    if pipeline.is_empty():
        st.warning("The index is empty.")
        if st.button("Ingest the corpus", type="primary"):
            with st.spinner("Ingesting..."):
                report = pipeline.ingest(reset=True)
            st.success(f"Indexed {report.documents} documents into {report.chunks} chunks.")
            st.cache_resource.clear()
            st.rerun()

    st.divider()
    top_n = st.slider("Passages in context", 1, 10, pipeline.config.rerank.top_n)
    st.caption(
        f"Abstention gates: relevance ≥ {pipeline.config.citations.min_top_score}, "
        f"question coverage ≥ {pipeline.config.citations.min_question_coverage}, "
        f"grounding ≥ {pipeline.config.citations.min_grounding}."
    )

st.header("Ask a question")

if pipeline.is_empty():
    st.info("Index the corpus from the sidebar to begin.")
    st.stop()

with st.expander("Indexed sources", expanded=False):
    for source in stats["sources"]:
        st.write(f"- `{source}`")

question = st.text_input(
    "Question",
    placeholder="How long do I have to submit dispute evidence?",
    label_visibility="collapsed",
)

if not question:
    st.stop()

with st.spinner("Retrieving and generating..."):
    answer = pipeline.answer(question, top_n=top_n)

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
            st.markdown(f"**`{citation.marker}`** · `{citation.source}`")
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
