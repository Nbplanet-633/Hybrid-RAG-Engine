import { useRef, useState, type DragEvent } from "react";

import type { LibraryInfo } from "../api/types";
import { formatBytes } from "../format";

interface Props {
  info: LibraryInfo | undefined;
  onFiles: (files: File[]) => void;
}

export function UploadZone({ info, onFiles }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const accept = info?.accepted_extensions.join(",");

  function handleDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length) onFiles(files);
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={
        "rounded-xl border-2 border-dashed px-4 py-5 text-center transition-colors " +
        (dragging
          ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
          : "border-slate-300 dark:border-slate-700")
      }
    >
      <p className="text-sm text-slate-600 dark:text-slate-300">Drop files here, or</p>
      <button
        type="button"
        onClick={() => input.current?.click()}
        className="mt-2 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:outline-none dark:focus-visible:ring-offset-slate-900"
      >
        Choose files
      </button>
      <input
        ref={input}
        type="file"
        multiple
        accept={accept}
        className="sr-only"
        aria-label="Upload documents"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length) onFiles(files);
          // Reset, so choosing the same file again still fires a change.
          event.target.value = "";
        }}
      />
      {info && (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
          PDF, Markdown, text, or HTML · up to {formatBytes(info.max_upload_bytes)}
        </p>
      )}
    </div>
  );
}
