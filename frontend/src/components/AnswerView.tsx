import { useState } from "react";

import type { AskResponse } from "../api/types";
import { ABSTAIN_EXPLANATIONS, basename, formatDuration } from "../format";
import { RichText } from "./RichText";

export function AnswerView({ answer }: { answer: AskResponse }) {
  const [highlighted, setHighlighted] = useState<string | null>(null);

  if (answer.abstained) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-5 dark:border-amber-500/30 dark:bg-amber-500/10">
        <p className="font-semibold text-amber-900 dark:text-amber-200">No answer</p>
        <p className="mt-1 text-amber-900 dark:text-amber-100">{answer.answer}</p>
        <p className="mt-3 text-sm text-amber-800 dark:text-amber-300">
          {ABSTAIN_EXPLANATIONS[answer.abstain_reason] ?? "The evidence was insufficient."}
        </p>
        <p className="mt-2 text-xs text-amber-700/80 dark:text-amber-300/70">
          This is deliberate: when the documents don't support an answer, the system says so instead
          of guessing.
        </p>
      </div>
    );
  }

  function showCitation(marker: string) {
    setHighlighted(marker);
    document
      .getElementById(`cite-${marker}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-xl border border-emerald-200 bg-white p-5 text-lg leading-relaxed shadow-sm dark:border-emerald-500/30 dark:bg-slate-900">
        <RichText text={answer.answer} onMarker={showCitation} />
      </div>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric
          label="Confidence"
          value={answer.confidence.toFixed(2)}
          hint="How strongly the retrieved passages match the question."
        />
        <Metric
          label="Grounding"
          value={answer.grounding_score.toFixed(2)}
          hint="How much of the answer is supported by the passages it cites."
        />
        <Metric label="Sources" value={String(answer.citations.length)} />
        <Metric
          label="Time"
          value={formatDuration(answer.total_ms)}
          hint="Search, reranking, and answer, end to end."
        />
      </dl>

      <section aria-labelledby="sources-heading">
        <h3 id="sources-heading" className="mb-2 text-sm font-semibold">
          Sources
        </h3>
        <ol className="flex flex-col gap-3">
          {answer.citations.map((citation) => (
            <li
              key={citation.marker}
              id={`cite-${citation.marker}`}
              className={
                "rounded-xl border bg-white p-4 transition-shadow dark:bg-slate-900 " +
                (highlighted === citation.marker
                  ? "border-indigo-400 ring-2 ring-indigo-400/40"
                  : "border-slate-200 dark:border-slate-800")
              }
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="rounded bg-indigo-100 px-1.5 font-mono text-xs font-medium text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">
                  {citation.marker}
                </span>
                <span className="font-medium">{basename(citation.source)}</span>
              </div>
              {citation.section && (
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{citation.section}</p>
              )}
              <blockquote className="mt-2 border-l-2 border-slate-300 pl-3 text-slate-700 dark:border-slate-600 dark:text-slate-300">
                <RichText text={citation.quote} />
              </blockquote>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div
      className="rounded-lg bg-white px-3 py-2 shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800"
      title={hint}
    >
      <dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="text-xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
