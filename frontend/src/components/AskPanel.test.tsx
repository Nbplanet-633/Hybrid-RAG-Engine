import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DocumentSummary } from "../api/types";
import { ALL_DOCUMENTS } from "../constants";
import { makeAnswer, renderWithQuery, stubFetch } from "../test/utils";
import { AskPanel } from "./AskPanel";

const docs: DocumentSummary[] = [
  {
    doc_id: "d1",
    filename: "disputes.md",
    title: "Disputes",
    content_type: "markdown",
    chunks: 2,
    size_bytes: 4096,
  },
  {
    doc_id: "d2",
    filename: "fees.md",
    title: "Fees",
    content_type: "markdown",
    chunks: 1,
    size_bytes: 2048,
  },
];

function renderPanel(props: Partial<Parameters<typeof AskPanel>[0]> = {}) {
  return renderWithQuery(
    <AskPanel
      documents={docs}
      loading={false}
      loadError={null}
      scope={ALL_DOCUMENTS}
      onScopeChange={() => {}}
      {...props}
    />,
  );
}

async function askQuestion(text: string) {
  await userEvent.type(screen.getByLabelText("Question"), text);
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("AskPanel", () => {
  it("sends the question with retrieval included, then shows the answer", async () => {
    const fetch = stubFetch(() => [200, makeAnswer()]);
    renderPanel();

    await askQuestion("How long for evidence?");

    const turn = await screen.findByRole("article");
    expect(within(turn).getByText("How long for evidence?")).toBeInTheDocument();
    expect(within(turn).getByText("Searched: All documents")).toBeInTheDocument();
    expect(await within(turn).findByText("2.9 s")).toBeInTheDocument();

    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("/library/ask");
    expect(JSON.parse(String(init?.body))).toEqual({
      question: "How long for evidence?",
      include_retrieval: true,
    });
    // Ready for the next question.
    expect(screen.getByLabelText("Question")).toHaveValue("");
    expect(screen.getByLabelText("Question")).toHaveFocus();
  });

  it("scopes the question to the selected document and labels the turn with it", async () => {
    const fetch = stubFetch(() => [200, makeAnswer()]);
    renderPanel({ scope: "d2" });

    await askQuestion("What is the fee?");

    expect(await screen.findByText("Searched: fees.md")).toBeInTheDocument();
    expect(JSON.parse(String(fetch.mock.calls[0]![1]?.body)).doc_ids).toEqual(["d2"]);
  });

  it("reports scope changes upward", async () => {
    stubFetch(() => [200, makeAnswer()]);
    const onScopeChange = vi.fn();
    renderPanel({ onScopeChange });

    await userEvent.selectOptions(screen.getByLabelText("Search in"), "fees.md");

    expect(onScopeChange).toHaveBeenCalledWith("d2");
  });

  it("keeps earlier turns, newest first, until cleared", async () => {
    stubFetch(() => [200, makeAnswer()]);
    renderPanel();

    await askQuestion("First question");
    await screen.findByText("2.9 s");
    await askQuestion("Second question");
    await waitFor(() => expect(screen.getAllByText("2.9 s")).toHaveLength(2));

    const turns = screen.getAllByRole("article");
    expect(within(turns[0]!).getByText("Second question")).toBeInTheDocument();
    expect(within(turns[1]!).getByText("First question")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryAllByRole("article")).toHaveLength(0);
  });

  it("allows one question at a time", async () => {
    let finish: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (finish = resolve))),
    );
    renderPanel();

    await askQuestion("Slow question");
    expect(await screen.findByText("Searching your documents…")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Question"), "Impatient follow-up");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();

    finish(new Response(JSON.stringify(makeAnswer()), { status: 200 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled());
  });

  it("shows a failed question as an error in its own turn", async () => {
    stubFetch(() => [502, { detail: "Generation failed: TimeoutError" }]);
    renderPanel();

    await askQuestion("Will this work?");

    const turn = await screen.findByRole("article");
    expect(await within(turn).findByRole("alert")).toHaveTextContent(
      "Generation failed: TimeoutError",
    );
  });

  it("offers the samples when the library is empty", async () => {
    const fetch = stubFetch((url) =>
      url === "/library/info"
        ? [200, { samples_available: true }]
        : [200, { added: [], rejected: {} }],
    );
    renderPanel({ documents: [] });

    await userEvent.click(await screen.findByRole("button", { name: "Try with sample documents" }));

    await waitFor(() =>
      expect(fetch.mock.calls.some(([url]) => url === "/library/samples")).toBe(true),
    );
    expect(screen.queryByLabelText("Question")).toBeNull();
  });

  it("shows why the library couldn't load", () => {
    renderPanel({ documents: [], loadError: new Error("Can't reach the server.") });
    expect(screen.getByRole("alert")).toHaveTextContent("Can't reach the server.");
  });
});
