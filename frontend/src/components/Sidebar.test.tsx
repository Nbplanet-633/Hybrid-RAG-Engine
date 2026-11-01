import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentSummary } from "../api/types";
import { apiKey } from "../apiKey";
import { renderWithQuery, stubFetch } from "../test/utils";
import { Sidebar } from "./Sidebar";

const INFO = {
  profile: "offline",
  embedder: "hashing",
  reranker: "lexical",
  generator: "extractive:extractive-v1",
  answer_mode: "quote",
  documents: 0,
  max_upload_bytes: 1024,
  accepted_extensions: [".md", ".pdf"],
  samples_available: true,
  requires_api_key: false,
};

const doc = (id: string, filename: string): DocumentSummary => ({
  doc_id: id,
  filename,
  title: filename,
  content_type: "markdown",
  chunks: 1,
  size_bytes: 512,
});

/**
 * A stand-in for XMLHttpRequest, which jsdom would otherwise send nowhere.
 * Each upload reports progress, then answers with `respond(filename)`.
 */
function stubXhr(respond: (filename: string) => [number, unknown]) {
  const sent: { headers: Record<string, string>; filename: string }[] = [];
  class FakeXhr {
    status = 0;
    response: unknown = null;
    responseType = "";
    upload: { onprogress?: (event: ProgressEvent) => void } = {};
    onload?: () => void;
    onerror?: () => void;
    private headers: Record<string, string> = {};
    open() {}
    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }
    send(form: FormData) {
      const file = form.get("file") as File;
      sent.push({ headers: this.headers, filename: file.name });
      const progress = (loaded: number) =>
        this.upload.onprogress?.({ lengthComputable: true, loaded, total: 10 } as ProgressEvent);
      setTimeout(() => {
        progress(5);
        progress(10);
        [this.status, this.response] = respond(file.name);
        this.onload?.();
      }, 0);
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return sent;
}

/** A small stateful server: info, a document list, and deletion. */
function stubLibrary(initial: DocumentSummary[]) {
  let documents = [...initial];
  return stubFetch((url, init) => {
    if (url === "/library/info") return [200, INFO];
    if (url === "/library/documents") return [200, documents];
    if (init?.method === "DELETE") {
      documents = documents.filter((d) => !url.endsWith(encodeURIComponent(d.doc_id)));
      return [204, null];
    }
    return [404, { detail: "unexpected" }];
  });
}

const md = (name: string, body = "# Hello\n\nWorld.") =>
  new File([body], name, { type: "text/markdown" });

beforeEach(() => {
  apiKey.forget();
});
afterEach(() => {
  apiKey.forget();
});

describe("Sidebar uploads", () => {
  it("uploads each file and reports its outcome", async () => {
    stubLibrary([]);
    const sent = stubXhr((name) =>
      name === "broken.pdf"
        ? [422, { detail: "Could not read broken.pdf: EOF marker not found" }]
        : [201, { ...doc("new", name), chunks: 3 }],
    );
    renderWithQuery(<Sidebar />);
    await screen.findByText("Quote mode");

    await userEvent.upload(screen.getByLabelText("Upload documents"), [
      md("notes.md"),
      new File(["%PDF-junk"], "broken.pdf", { type: "application/pdf" }),
    ]);

    const item = (name: string) => screen.getByText(name, { selector: "span" }).closest("li")!;
    await waitFor(() => expect(item("notes.md")).toHaveTextContent("Added"));
    expect(item("notes.md")).toHaveTextContent("3 chunks");
    await waitFor(() => expect(item("broken.pdf")).toHaveTextContent("EOF marker not found"));
    expect(sent.map((s) => s.filename)).toEqual(["notes.md", "broken.pdf"]); // one at a time
  });

  it("rejects files in the browser that the server would refuse", async () => {
    stubLibrary([]);
    const sent = stubXhr(() => [201, doc("x", "x.md")]);
    renderWithQuery(<Sidebar />);
    await screen.findByText("Quote mode");

    // applyAccept: false, so the picker's accept filter doesn't hide the bad file.
    await userEvent.upload(
      screen.getByLabelText("Upload documents"),
      [new File(["x"], "photo.png"), md("empty.md", ""), md("big.md", "x".repeat(2048))],
      { applyAccept: false },
    );

    await screen.findByText(/Unsupported file type/);
    expect(screen.getByText("This file is empty.")).toBeInTheDocument();
    expect(screen.getByText(/Larger than the/)).toBeInTheDocument();
    expect(sent).toHaveLength(0); // nothing reached the network
  });

  it("accepts dropped files, sends the API key, and clears finished items", async () => {
    stubLibrary([]);
    apiKey.set("k-123", false);
    const sent = stubXhr((name) => [201, doc("d", name)]);
    renderWithQuery(<Sidebar />);
    await screen.findByText("Quote mode");

    fireEvent.drop(screen.getByText("Drop files here, or").parentElement!, {
      dataTransfer: { files: [md("dropped.md")] },
    });

    await waitFor(() => expect(screen.getByText("Added")).toBeInTheDocument());
    expect(sent[0]!.headers["X-API-Key"]).toBe("k-123");

    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByText("Added")).toBeNull();
  });
});

describe("Sidebar document list", () => {
  it("removes a document and refreshes the list", async () => {
    const fetch = stubLibrary([doc("a", "alpha.md"), doc("b", "beta.md")]);
    renderWithQuery(<Sidebar />);

    await userEvent.click(await screen.findByRole("button", { name: "Remove alpha.md" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Remove alpha.md" })).toBeNull(),
    );
    expect(screen.getByRole("button", { name: "Remove beta.md" })).toBeInTheDocument();
    expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true);
  });

  it("lists each document with its size and chunk count", async () => {
    stubLibrary([{ ...doc("a", "alpha.md"), chunks: 2, size_bytes: 2048 }]);
    renderWithQuery(<Sidebar />);
    const entry = (await screen.findByText("alpha.md")).closest("li")!;
    expect(within(entry).getByText("2 chunks · 2 KB")).toBeInTheDocument();
  });

  it("says so when the library is empty", async () => {
    stubLibrary([]);
    renderWithQuery(<Sidebar />);
    expect(await screen.findByText("No documents yet.")).toBeInTheDocument();
  });
});
