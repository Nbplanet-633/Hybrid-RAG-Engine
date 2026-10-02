import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./App";
import { apiKey } from "./apiKey";
import { renderWithQuery, stubFetch } from "./test/utils";

const info = (requiresKey: boolean) => ({
  profile: "offline",
  embedder: "hashing",
  reranker: "lexical",
  generator: "extractive:extractive-v1",
  answer_mode: "quote",
  documents: 0,
  max_upload_bytes: 1024,
  accepted_extensions: [".md"],
  samples_available: false,
  requires_api_key: requiresKey,
});

/** A server whose library endpoints accept only `validKey` (or anything, if null). */
function server(validKey: string | null) {
  return stubFetch((url, init) => {
    if (url === "/library/info") return [200, info(validKey !== null)];
    const sent = new Headers(init?.headers).get("X-API-Key");
    if (validKey !== null && sent !== validKey)
      return [401, { detail: "Invalid or missing X-API-Key" }];
    return [200, []];
  });
}

afterEach(() => {
  apiKey.forget();
});

describe("API key gate", () => {
  it("goes straight to the app when the server needs no key", async () => {
    server(null);
    renderWithQuery(<App />);
    expect(await screen.findByText("Start by adding a document")).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).toBeNull();
  });

  it("asks for the key first, then sends it with every request", async () => {
    const fetch = server("s3cret");
    renderWithQuery(<App />);

    await userEvent.type(await screen.findByLabelText("API key"), "s3cret");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("Start by adding a document")).toBeInTheDocument();
    const keyed = fetch.mock.calls.filter(([url]) => String(url) !== "/library/info");
    expect(keyed.length).toBeGreaterThan(0);
    for (const [, init] of keyed) {
      expect(new Headers(init?.headers).get("X-API-Key")).toBe("s3cret");
    }
    // Nothing keyed was requested before the key existed.
    expect(
      fetch.mock.calls.filter(([, init]) => !new Headers(init?.headers).get("X-API-Key")),
    ).toHaveLength(1);
  });

  it("asks again, saying why, when the server rejects the key", async () => {
    server("s3cret");
    renderWithQuery(<App />);

    await userEvent.type(await screen.findByLabelText("API key"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("didn't accept that key");
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });
});
