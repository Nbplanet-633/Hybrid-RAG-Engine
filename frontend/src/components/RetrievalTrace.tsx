import type { Citation, RetrievedPassage } from "../api/types";
import { basename } from "../format";

const FOUND_BY: Record<RetrievedPassage["retriever"], { label: string; hint: string }> = {
  hybrid: { label: "Both", hint: "Found by meaning search and keyword search" },
  dense: { label: "Meaning", hint: "Found by meaning (embedding) search only" },
  lexical: { label: "Keywords", hint: "Found by keyword (BM25) search only" },
};

/**
 * How the answer was found: every passage that reached the reranker, in final
 * order, with where each search leg ranked it. "Both" is the strongest signal,
 * since the two legs fail in different ways.
 */
export function RetrievalTrace({
  passages,
  citations,
}: {
  passages: RetrievedPassage[];
  citations: Citation[];
}) {
  if (!passages.length) return null;
  const citedAs = new Map(citations.map((c) => [c.chunk_id, c.marker]));

  return (
    <details className="group rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium select-none">
        How this was found
        <span className="font-normal text-slate-500 dark:text-slate-400">
          {" "}
          · {passages.length} {passages.length === 1 ? "passage" : "passages"} ranked
        </span>
      </summary>
      <div className="overflow-x-auto border-t border-slate-200 dark:border-slate-800">
        <table className="w-full text-left text-xs">
          <thead className="text-slate-500 dark:text-slate-400">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">#</th>
              <th scope="col" className="px-3 py-2 font-medium">Passage</th>
              <th scope="col" className="px-3 py-2 font-medium">Found by</th>
              <th scope="col" className="px-3 py-2 text-right font-medium" title="Rank in the meaning (embedding) search">
                Meaning
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium" title="Rank in the keyword (BM25) search">
                Keywords
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium" title="Final score after reranking">
                Score
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {passages.map((passage) => {
              const marker = citedAs.get(passage.chunk_id);
              const foundBy = FOUND_BY[passage.retriever];
              return (
                <tr key={passage.chunk_id} className={marker ? "bg-indigo-50/60 dark:bg-indigo-500/10" : ""}>
                  <td className="px-3 py-2 tabular-nums">{passage.rank}</td>
                  <td className="max-w-[16rem] px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      {marker && (
                        <span className="rounded bg-indigo-100 px-1 font-mono text-[10px] font-medium text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">
                          {marker}
                        </span>
                      )}
                      <span className="truncate font-medium">{basename(passage.source)}</span>
                    </div>
                    {passage.section && (
                      <div className="truncate text-slate-500 dark:text-slate-400" title={passage.section}>
                        {passage.section}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2" title={foundBy.hint}>
                    {foundBy.label}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{passage.dense_rank ?? "–"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{passage.lexical_rank ?? "–"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{passage.score.toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}
