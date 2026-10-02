import { describe, expect, it } from "vitest";

import { precheck } from "./hooks";
import type { LibraryInfo } from "./types";

const info: LibraryInfo = {
  profile: "offline",
  embedder: "hashing",
  reranker: "lexical",
  generator: "extractive:extractive-v1",
  answer_mode: "quote",
  documents: 0,
  max_upload_bytes: 1024,
  accepted_extensions: [".md", ".pdf", ".txt"],
  samples_available: true,
  requires_api_key: false,
};

const file = (name: string, size: number) => new File([new Uint8Array(size)], name);

describe("precheck", () => {
  it("accepts a supported file within the limit", () => {
    expect(precheck(file("notes.md", 10), info)).toBeUndefined();
  });

  it("matches extensions case-insensitively", () => {
    expect(precheck(file("REPORT.PDF", 10), info)).toBeUndefined();
  });

  it("rejects unsupported types, naming what is accepted", () => {
    expect(precheck(file("photo.png", 10), info)).toMatch(/Unsupported.*\.md, \.pdf, \.txt/);
    expect(precheck(file("no-extension", 10), info)).toMatch(/Unsupported/);
  });

  it("rejects empty files", () => {
    expect(precheck(file("empty.md", 0), info)).toBe("This file is empty.");
  });

  it("rejects files over the server's limit, and accepts one exactly at it", () => {
    expect(precheck(file("big.md", 1025), info)).toMatch(/limit/);
    expect(precheck(file("edge.md", 1024), info)).toBeUndefined();
  });
});
