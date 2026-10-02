import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { api } from "./client";
import type { AskRequest, LibraryInfo } from "./types";

const keys = {
  info: ["library", "info"] as const,
  documents: ["library", "documents"] as const,
};

export function useLibraryInfo() {
  return useQuery({ queryKey: keys.info, queryFn: api.info, staleTime: 60_000 });
}

export function useDocuments() {
  return useQuery({ queryKey: keys.documents, queryFn: api.documents });
}

/** Everything that changes the library also changes its document count in /info. */
function useInvalidateLibrary() {
  const queryClient = useQueryClient();
  return useCallback(() => queryClient.invalidateQueries({ queryKey: ["library"] }), [queryClient]);
}

export function useDeleteDocument() {
  const invalidate = useInvalidateLibrary();
  return useMutation({ mutationFn: api.deleteDocument, onSettled: invalidate });
}

export function useAddSamples() {
  const invalidate = useInvalidateLibrary();
  return useMutation({ mutationFn: api.addSamples, onSettled: invalidate });
}

export function useAsk() {
  return useMutation({ mutationFn: (body: AskRequest) => api.ask(body) });
}

let uploadSeq = 0;

export interface UploadItem {
  id: number;
  name: string;
  progress: number;
  status: "queued" | "uploading" | "indexing" | "done" | "error";
  message?: string;
}

/**
 * Uploads files one at a time, tracking each one's progress and outcome.
 *
 * Sequential on purpose: the server serialises ingestion behind one lock anyway,
 * so parallel uploads would only make every file's progress bar slower. Files the
 * server would reject for type or size are failed here, without a round trip.
 */
export function useUploads(info: LibraryInfo | undefined) {
  const invalidate = useInvalidateLibrary();
  const [items, setItems] = useState<UploadItem[]>([]);

  const update = useCallback((id: number, patch: Partial<UploadItem>) => {
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  const uploadFiles = useCallback(
    async (files: File[]) => {
      const batch = files.map((file) => {
        const item: UploadItem = { id: ++uploadSeq, name: file.name, progress: 0, status: "queued" };
        return { file, item };
      });
      setItems((current) => [...batch.map((b) => b.item), ...current].slice(0, 20));

      for (const { file, item } of batch) {
        const problem = info ? precheck(file, info) : undefined;
        if (problem) {
          update(item.id, { status: "error", message: problem });
          continue;
        }
        update(item.id, { status: "uploading" });
        try {
          const summary = await api.upload(file, (fraction) =>
            // The last stretch is server-side parsing and embedding, not transfer.
            update(item.id, fraction >= 1 ? { status: "indexing", progress: 1 } : { progress: fraction }),
          );
          update(item.id, {
            status: "done",
            progress: 1,
            message: `${summary.chunks} ${summary.chunks === 1 ? "chunk" : "chunks"}`,
          });
        } catch (error) {
          update(item.id, { status: "error", message: (error as Error).message });
        }
        void invalidate();
      }
    },
    [info, invalidate, update],
  );

  const clearFinished = useCallback(() => {
    setItems((current) => current.filter((item) => item.status !== "done" && item.status !== "error"));
  }, []);

  return { items, uploadFiles, clearFinished };
}

function precheck(file: File, info: LibraryInfo): string | undefined {
  const dot = file.name.lastIndexOf(".");
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!info.accepted_extensions.includes(extension)) {
    return `Unsupported file type. Use ${info.accepted_extensions.join(", ")}.`;
  }
  if (file.size === 0) return "This file is empty.";
  if (file.size > info.max_upload_bytes) {
    return `Larger than the ${Math.round(info.max_upload_bytes / 1024 / 1024)} MB limit.`;
  }
  return undefined;
}
