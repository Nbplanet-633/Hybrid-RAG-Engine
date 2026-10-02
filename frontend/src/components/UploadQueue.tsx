import type { UploadItem } from "../api/hooks";

const STATUS_TEXT: Record<UploadItem["status"], string> = {
  queued: "Waiting…",
  uploading: "Uploading…",
  indexing: "Reading and indexing…",
  done: "Added",
  error: "Not added",
};

export function UploadQueue({ items, onClear }: { items: UploadItem[]; onClear: () => void }) {
  if (!items.length) return null;
  const finished = items.some((item) => item.status === "done" || item.status === "error");

  return (
    <div aria-live="polite" className="flex flex-col gap-2">
      <ul className="flex flex-col gap-2">
        {items.map((item) => (
          <li
            key={item.id}
            className={
              "rounded-lg px-3 py-2 text-xs " +
              (item.status === "error"
                ? "bg-red-50 text-red-800 dark:bg-red-500/10 dark:text-red-300"
                : item.status === "done"
                  ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300"
                  : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300")
            }
          >
            <div className="flex justify-between gap-2">
              <span className="truncate font-medium" title={item.name}>
                {item.name}
              </span>
              <span className="shrink-0">{STATUS_TEXT[item.status]}</span>
            </div>
            {item.status === "uploading" && (
              <div className="mt-1.5 h-1 overflow-hidden rounded bg-slate-200 dark:bg-slate-700">
                <div
                  className="h-full bg-indigo-500 transition-[width]"
                  style={{ width: `${Math.round(item.progress * 100)}%` }}
                />
              </div>
            )}
            {item.status === "indexing" && (
              <div className="mt-1.5 h-1 animate-pulse rounded bg-indigo-400/60" />
            )}
            {item.message && <p className="mt-1 break-words">{item.message}</p>}
          </li>
        ))}
      </ul>
      {finished && (
        <button
          type="button"
          onClick={onClear}
          className="self-end text-xs text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
        >
          Clear
        </button>
      )}
    </div>
  );
}
