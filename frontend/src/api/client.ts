import type {
  AskRequest,
  AskResponse,
  DocumentSummary,
  LibraryInfo,
  SamplesResponse,
} from "./types";

/** An HTTP error from the API, carrying the server's own explanation. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * FastAPI reports errors as `{"detail": ...}`: a string for errors the API
 * raises itself, or a list of field errors when request validation fails.
 */
function detailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : ""))
        .filter(Boolean)
        .join("; ");
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is `askmydocs serve` running?");
  }
  if (response.status === 204) return undefined as T;

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, detailMessage(body, `Request failed (${response.status})`));
  }
  return body as T;
}

export const api = {
  info: () => request<LibraryInfo>("/library/info"),

  documents: () => request<DocumentSummary[]>("/library/documents"),

  deleteDocument: (docId: string) =>
    request<void>(`/library/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }),

  addSamples: () => request<SamplesResponse>("/library/samples", { method: "POST" }),

  ask: (body: AskRequest) =>
    request<AskResponse>("/library/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /**
   * Upload one file. XMLHttpRequest rather than fetch, because fetch cannot
   * report upload progress, and a large PDF over a slow link needs it.
   */
  upload: (file: File, onProgress?: (fraction: number) => void) =>
    new Promise<DocumentSummary>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/library/documents");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.responseType = "json";
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(event.loaded / event.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(xhr.response as DocumentSummary);
        } else {
          reject(
            new ApiError(xhr.status, detailMessage(xhr.response, `Upload failed (${xhr.status})`)),
          );
        }
      };
      xhr.onerror = () => reject(new ApiError(0, "Can't reach the server."));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    }),
};
