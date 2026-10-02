import { useDeleteDocument, useDocuments } from "../api/hooks";
import { formatBytes, plural } from "../format";

export function DocumentList() {
  const documents = useDocuments();
  const remove = useDeleteDocument();

  if (documents.isPending) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }
  if (documents.error) {
    return <p className="text-sm text-red-600 dark:text-red-400">{documents.error.message}</p>;
  }
  if (!documents.data.length) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">No documents yet.</p>;
  }

  return (
    <>
      <ul className="flex flex-col gap-1">
        {documents.data.map((doc) => {
          const deleting = remove.isPending && remove.variables === doc.doc_id;
          return (
            <li
              key={doc.doc_id}
              className={
                "group flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 " +
                (deleting ? "opacity-50" : "")
              }
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium" title={doc.title || doc.filename}>
                  {doc.filename}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {plural(doc.chunks, "chunk")} · {formatBytes(doc.size_bytes)}
                </p>
              </div>
              <button
                type="button"
                disabled={deleting}
                onClick={() => remove.mutate(doc.doc_id)}
                aria-label={`Remove ${doc.filename}`}
                title="Remove"
                className="rounded-md p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600 focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:outline-none dark:hover:bg-red-500/10"
              >
                <svg viewBox="0 0 20 20" fill="currentColor" className="size-4" aria-hidden="true">
                  <path
                    fillRule="evenodd"
                    d="M8.75 1A2.75 2.75 0 0 0 6 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 1 0 .23 1.482l.149-.022.841 10.518A2.75 2.75 0 0 0 7.596 19h4.807a2.75 2.75 0 0 0 2.742-2.53l.841-10.52.149.023a.75.75 0 0 0 .23-1.482A41.03 41.03 0 0 0 14 4.193V3.75A2.75 2.75 0 0 0 11.25 1h-2.5ZM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4ZM8.58 7.72a.75.75 0 0 0-1.5.06l.3 7.5a.75.75 0 1 0 1.5-.06l-.3-7.5Zm4.34.06a.75.75 0 1 0-1.5-.06l-.3 7.5a.75.75 0 1 0 1.5.06l.3-7.5Z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            </li>
          );
        })}
      </ul>
      {remove.error && (
        <p className="text-xs text-red-600 dark:text-red-400">{remove.error.message}</p>
      )}
    </>
  );
}
