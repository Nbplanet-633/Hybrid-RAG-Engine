import { useLibraryInfo, useUploads } from "../api/hooks";
import { DocumentList } from "./DocumentList";
import { EngineBadge } from "./EngineBadge";
import { UploadQueue } from "./UploadQueue";
import { UploadZone } from "./UploadZone";

export function Sidebar() {
  const info = useLibraryInfo();
  const uploads = useUploads(info.data);

  return (
    <aside className="flex w-full shrink-0 flex-col gap-6 border-b border-slate-200 bg-white px-5 py-6 md:sticky md:top-0 md:h-screen md:w-80 md:overflow-y-auto md:border-r md:border-b-0 dark:border-slate-800 dark:bg-slate-900">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Ask My Docs</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Upload documents, then ask questions. Every answer cites its source.
        </p>
        {info.data && <EngineBadge info={info.data} />}
      </header>

      <section aria-labelledby="documents-heading" className="flex flex-col gap-3">
        <h2
          id="documents-heading"
          className="text-xs font-semibold tracking-wider text-slate-500 uppercase dark:text-slate-400"
        >
          Your documents
        </h2>
        <UploadZone info={info.data} onFiles={uploads.uploadFiles} />
        <UploadQueue items={uploads.items} onClear={uploads.clearFinished} />
        <DocumentList />
      </section>
    </aside>
  );
}
