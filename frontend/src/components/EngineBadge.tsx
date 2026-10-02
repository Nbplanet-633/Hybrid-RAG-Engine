import type { LibraryInfo } from "../api/types";
import { apiKey } from "../apiKey";

const PROFILE_LABELS: Record<string, string> = {
  offline: "Offline",
  "full-retrieval": "Full retrieval",
  full: "Full",
};

export function EngineBadge({ info }: { info: LibraryInfo }) {
  const quote = info.answer_mode === "quote";
  return (
    <div className="mt-4 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{PROFILE_LABELS[info.profile] ?? info.profile}</span>
        <span
          className={
            "rounded-full px-2 py-0.5 font-medium " +
            (quote
              ? "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"
              : "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300")
          }
          title={
            quote
              ? "Answers are the most relevant sentences quoted from your documents."
              : "A language model writes each answer from the retrieved passages."
          }
        >
          {quote ? "Quote mode" : "Written answers"}
        </span>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-slate-500 select-none dark:text-slate-400">
          Engine details
        </summary>
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 font-mono text-[11px] break-all">
          <dt className="text-slate-500">embed</dt>
          <dd>{info.embedder}</dd>
          <dt className="text-slate-500">rerank</dt>
          <dd>{info.reranker}</dd>
          <dt className="text-slate-500">answer</dt>
          <dd>{info.generator}</dd>
        </dl>
      </details>
      {info.requires_api_key && (
        <button
          type="button"
          onClick={() => apiKey.forget()}
          className="mt-1 text-slate-500 underline-offset-2 hover:text-slate-900 hover:underline dark:text-slate-400 dark:hover:text-slate-100"
        >
          Forget API key
        </button>
      )}
    </div>
  );
}
