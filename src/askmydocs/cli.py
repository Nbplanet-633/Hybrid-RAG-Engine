"""Command-line interface.

askmydocs ingest                     # index ./data/corpus
askmydocs ingest docs/ --reset       # re-index from scratch
askmydocs ask "How long do I have to respond to a dispute?"
askmydocs ask "..." --show-retrieval # inspect the retrieval trace
askmydocs stats
askmydocs prompts
askmydocs serve
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from askmydocs.config import load_config, missing_requirements
from askmydocs.pipeline import RAGPipeline

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Ask My Docs — hybrid-retrieval RAG with enforced citations.",
)
console = Console()

ProfileOption = typer.Option(
    None,
    "--profile",
    "-p",
    help="Config profile: offline | full-retrieval | full.",
)
ConfigOption = typer.Option(None, "--config", "-c", help="Path to app.yaml.")


def _build(profile: str | None, config_path: Path | None) -> RAGPipeline:
    try:
        return RAGPipeline.from_config(load_config(path=config_path, profile=profile))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, don't traceback
        # Escape the exception text: Rich would otherwise treat a bracketed
        # fragment as a style tag and silently swallow it, turning the advice
        # "pip install 'ask-my-docs[models]'" into the wrong command.
        console.print(f"[red]Failed to initialise:[/red] {type(exc).__name__}: {escape(str(exc))}")
        raise typer.Exit(code=2) from exc


@app.command()
def ingest(
    targets: list[str] = typer.Argument(
        None, help="Files, directories, or URLs. Defaults to corpus_dir."
    ),
    reset: bool = typer.Option(False, "--reset", help="Drop the existing index first."),
    profile: str | None = ProfileOption,
    config: Path | None = ConfigOption,
) -> None:
    """Load, chunk, embed, and index documents."""
    pipeline = _build(profile, config)
    with console.status("Ingesting..."):
        report = pipeline.ingest(targets or None, reset=reset)

    table = Table(title="Ingestion complete", show_header=False, box=None)
    table.add_row("Documents indexed", str(report.documents))
    table.add_row("Chunks written", str(report.chunks))
    table.add_row("Skipped (unchanged)", str(report.skipped_unchanged))
    table.add_row("Total chunks", str(len(pipeline.chunk_store)))
    table.add_row("Duration", f"{report.duration_s}s")
    console.print(table)
    if report.skipped_unchanged:
        console.print(
            f"[dim]{report.skipped_unchanged} document(s) unchanged since the last run "
            f"and were not re-embedded. Use --reset to force.[/dim]"
        )


@app.command()
def ask(
    question: str = typer.Argument(..., help="The question to answer."),
    show_retrieval: bool = typer.Option(
        False, "--show-retrieval", help="Print the retrieval trace."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the full Answer object as JSON."),
    top_n: int | None = typer.Option(None, "--top-n", help="Override the reranked context size."),
    profile: str | None = ProfileOption,
    config: Path | None = ConfigOption,
) -> None:
    """Answer a question against the indexed documents."""
    pipeline = _build(profile, config)
    if pipeline.is_empty():
        console.print("[red]The index is empty.[/red] Run [bold]askmydocs ingest[/bold] first.")
        raise typer.Exit(code=2)

    answer = pipeline.answer(question, top_n=top_n)

    if as_json:
        console.print_json(answer.model_dump_json())
        raise typer.Exit(code=0)

    # Generated text is escaped before rendering: it is model output, and a
    # bracketed fragment in it must be displayed, not interpreted as a Rich style
    # tag. Citation markers like [S1] are the whole point of the answer.
    if answer.abstained:
        console.print(
            Panel(
                f"{escape(answer.text)}\n\n[dim]Reason: {answer.abstain_reason}[/dim]",
                title="No answer (abstained)",
                border_style="yellow",
            )
        )
    else:
        console.print(Panel(escape(answer.text), title="Answer", border_style="green"))
        citations = Table(title="Citations", show_lines=False)
        citations.add_column("Marker", style="cyan", no_wrap=True)
        citations.add_column("Source")
        citations.add_column("Section")
        citations.add_column("Score", justify="right")
        for citation in answer.citations:
            citations.add_row(
                citation.marker, citation.source, citation.section or "—", f"{citation.score:.3f}"
            )
        console.print(citations)
        console.print(
            f"[dim]confidence={answer.confidence:.2f}  grounding={answer.grounding_score:.2f}  "
            f"model={answer.model}  prompt={answer.prompt_name}.{answer.prompt_version}  "
            f"{answer.latency_ms:.0f}ms[/dim]"
        )

    if show_retrieval:
        trace = Table(title="Retrieval trace (post-rerank)")
        trace.add_column("#", justify="right", style="dim")
        trace.add_column("Score", justify="right")
        trace.add_column("Dense", justify="right", style="dim")
        trace.add_column("BM25", justify="right", style="dim")
        trace.add_column("Leg")
        trace.add_column("Source")
        trace.add_column("Section")
        for index, item in enumerate(answer.retrieved, start=1):
            trace.add_row(
                str(index),
                f"{item.score:.3f}",
                "—" if item.dense_rank is None else str(item.dense_rank),
                "—" if item.lexical_rank is None else str(item.lexical_rank),
                item.retriever,
                Path(item.chunk.source).name,
                (item.chunk.section or "—")[:44],
            )
        console.print(trace)


@app.command()
def stats(
    profile: str | None = ProfileOption,
    config: Path | None = ConfigOption,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show index and configuration state."""
    pipeline = _build(profile, config)
    info = pipeline.stats()
    if as_json:
        console.print_json(json.dumps(info))
        raise typer.Exit(code=0)

    table = Table(title="Ask My Docs — index state", show_header=False, box=None)
    for key in (
        "profile",
        "documents",
        "chunks",
        "vectors",
        "bm25_documents",
        "embedder",
        "vector_store",
        "reranker",
        "generator",
        "prompt",
        "prompt_hash",
        "storage_dir",
        "updated_at",
    ):
        table.add_row(key, str(info.get(key)))
    console.print(table)
    if info["sources"]:
        console.print(f"[dim]{len(info['sources'])} source file(s) indexed[/dim]")


@app.command()
def prompts(
    profile: str | None = ProfileOption,
    config: Path | None = ConfigOption,
) -> None:
    """List the versioned prompts available to the pipeline."""
    cfg = load_config(path=config, profile=profile)
    from askmydocs.generation.prompts import PromptLibrary

    library = PromptLibrary(cfg.generation.prompts_dir)
    table = Table(title="Prompt registry")
    table.add_column("Name", style="cyan")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Hash", style="dim")
    table.add_column("Variables", style="dim")
    for entry in library.describe():
        table.add_row(
            entry["name"],
            entry["version"],
            entry["status"],
            entry["content_hash"],
            ", ".join(entry["variables"]),
        )
    console.print(table)
    active = library.get(cfg.generation.prompt, cfg.generation.prompt_version)
    console.print(f"[dim]Active: {active.name}.{active.version} ({active.content_hash})[/dim]")


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host", help="Bind address. Default from config."),
    port: int | None = typer.Option(None, "--port", help="Port to listen on. Default 8000."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
    profile: str | None = ProfileOption,
    config: Path | None = ConfigOption,
) -> None:
    """Run the HTTP API."""
    import os

    import uvicorn

    cfg = load_config(path=config, profile=profile)
    # Refuse to start a profile this machine cannot run. The API would come up
    # anyway and fail on the first request; saying what to install is kinder.
    if missing := missing_requirements(cfg):
        console.print(
            f"[red]The {cfg.profile} profile isn't set up on this machine.[/red] It needs:"
        )
        for item in missing:
            console.print(f"  - {escape(item)}")
        console.print("Install what's missing, or pick another profile with --profile.")
        raise typer.Exit(code=1)

    # The API builds its own pipeline at startup, so pass the selection through
    # the environment rather than trying to hand it a live object across processes.
    os.environ["ASKMYDOCS_PROFILE"] = cfg.profile
    if config:
        os.environ["ASKMYDOCS_CONFIG"] = str(config)

    console.print(
        f"[green]Serving[/green] profile=[bold]{cfg.profile}[/bold] on "
        f"http://{host or cfg.api.host}:{port or cfg.api.port}  (docs at /docs)"
    )
    uvicorn.run(
        "askmydocs.api.main:app",
        host=host or cfg.api.host,
        port=port or cfg.api.port,
        reload=reload,
    )


@app.command()
def version() -> None:
    """Print the package version."""
    from askmydocs import __version__

    console.print(__version__)


def main() -> None:  # pragma: no cover - console-script shim
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
