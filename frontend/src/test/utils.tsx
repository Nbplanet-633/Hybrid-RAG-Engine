import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import type { AskResponse } from "../api/types";

/** Render with a fresh query cache per test, with retries off so failures surface at once. */
export function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** Stub fetch with a handler from URL to [status, JSON body]. Returns the mock for assertions. */
export function stubFetch(handler: (url: string, init?: RequestInit) => [number, unknown]) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const [status, body] = handler(String(input), init);
    return new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

export function makeAnswer(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    question: "How long do I have to submit evidence?",
    answer: "**You have 7 days from the `dispute.created` webhook.** [S1]",
    abstained: false,
    abstain_reason: "",
    citations: [
      {
        marker: "S1",
        source: "storage/uploads/files/disputes.md",
        section: "Disputes > Evidence window",
        title: "Disputes",
        quote: "**You have 7 days from the `dispute.created` webhook.**",
        score: 0.9,
        chunk_id: "disputes::0001",
      },
    ],
    confidence: 0.88,
    grounding_score: 1,
    model: "extractive-v1",
    prompt: "answer.v2",
    latency_ms: 40,
    total_ms: 2850,
    input_tokens: 600,
    output_tokens: 20,
    retrieval: [
      {
        rank: 1,
        source: "storage/uploads/files/disputes.md",
        section: "Disputes > Evidence window",
        chunk_id: "disputes::0001",
        score: 0.91,
        retriever: "hybrid",
        dense_rank: 1,
        lexical_rank: 2,
      },
      {
        rank: 2,
        // A Windows-style path: the UI must still show just the filename.
        source: String.raw`storage\uploads\files\fees.md`,
        section: "Fees",
        chunk_id: "fees::0000",
        score: -1.5,
        retriever: "lexical",
        dense_rank: null,
        lexical_rank: 1,
      },
    ],
    ...overrides,
  };
}
