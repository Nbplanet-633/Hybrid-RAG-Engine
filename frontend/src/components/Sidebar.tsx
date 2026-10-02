import { useState } from "react";

import { useDocuments, useLibraryInfo, useUploads } from "../api/hooks";
import { DocumentList } from "./DocumentList";
import { EngineBadge } from "./EngineBadge";
import { ThemeToggle } from "./ThemeToggle";
import { UploadQueue } from "./UploadQueue";
import { UploadZone } from "./UploadZone";

export function Sidebar() {
  const info = useLibraryInfo();
  const documents = useDocuments();
  const uploads = useUploads(info.data);
  // Phones only: the library starts collapsed so the question box is on screen
  // first. From md up the panel is always open and this toggle is hidden.
  const [open, setOpen] = useState(false);
  const count = documents.data?.length;
  // An empty library stays open: uploading is the only thing to do.
  const expanded = open || count === 0;

  return (
    <aside className="flex w-full shrink-0 flex-col gap-5 border-b border-slate-200 bg-white px-5 py-5 md:sticky md:top-0 md:h-screen md:w-80 md:gap-6 md:overflow-y-auto md:border-r md:border-b-0 md:py-6 dark:border-slate-800 dark:bg-slate-900">
      <header>
        <div className="flex items-start justify-between gap-2">
          <h1 className="text-xl font-semibold tracking-tight">Ask My Docs</h1>
          <ThemeToggle />
        </div>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Upload documents, then ask questions. Every answer cites its source.
        </p>
        {info.data && <EngineBadge info={info.data} />}
      </header>

      <section aria-labelledby="documents-heading" className="flex flex-col gap-3">
        <h2 id="documents-heading" className="contents">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={expanded}
            aria-controls="library-panel"
            className="flex items-center justify-between rounded-lg bg-slate-100 px-3 py-2 text-sm font-medium md:hidden dark:bg-slate-800"
          >
            <span>Your documents{count !== undefined && ` (${count})`}</span>
            <span aria-hidden="true">{expanded ? "▴" : "▾"}</span>
          </button>
          <span className="hidden text-xs font-semibold tracking-wider text-slate-500 uppercase md:block dark:text-slate-400">
            Your documents
          </span>
        </h2>
        <div
          id="library-panel"
          className={(expanded ? "flex" : "hidden") + " flex-col gap-3 md:flex"}
        >
          <UploadZone info={info.data} onFiles={uploads.uploadFiles} />
          <UploadQueue items={uploads.items} onClear={uploads.clearFinished} />
          <DocumentList />
        </div>
      </section>
    </aside>
  );
}
