import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeAnswer, renderWithQuery, stubFetch } from "../test/utils";
import { AnswerView } from "./AnswerView";

describe("AnswerView", () => {
  it("shows the answer, its end-to-end time, and its sources by filename", () => {
    renderWithQuery(<AnswerView answer={makeAnswer()} turnId={1} />);
    // Once in the answer, once as its source's quote: they are the same sentence.
    expect(screen.getAllByText(/You have 7 days/, { selector: "strong" })).toHaveLength(2);
    // total_ms, not the generation-only latency_ms.
    expect(screen.getByText("2.9 s")).toBeInTheDocument();
    const sources = screen.getByRole("region", { name: "Sources" });
    expect(within(sources).getByText("disputes.md")).toBeInTheDocument();
  });

  it("highlights a source when its marker is clicked", async () => {
    renderWithQuery(<AnswerView answer={makeAnswer()} turnId={7} />);
    await userEvent.click(screen.getByRole("button", { name: "Go to source 1" }));
    expect(document.getElementById("cite-7-S1")).toHaveClass("ring-2");
  });

  it("loads the full passage on request and marks the quoted sentence", async () => {
    const fetch = stubFetch(() => [
      200,
      {
        chunk_id: "disputes::0001",
        doc_id: "disputes",
        source: "disputes.md",
        title: "Disputes",
        section: "Evidence window",
        text: "Disputes open on a webhook. **You have 7 days from the `dispute.created` webhook.** Evidence cannot be amended.",
      },
    ]);
    renderWithQuery(<AnswerView answer={makeAnswer()} turnId={1} />);
    expect(fetch).not.toHaveBeenCalled(); // passages load lazily

    await userEvent.click(screen.getByRole("button", { name: "Show full passage" }));
    const mark = await screen.findByText(/You have 7 days/, { selector: "mark strong" });
    expect(mark).toBeInTheDocument();
    expect(screen.getByText(/Evidence cannot be amended/)).toBeInTheDocument();
    expect(fetch.mock.calls[0]?.[0]).toBe("/library/passages/disputes%3A%3A0001");
  });

  it("explains an abstention in words, and still shows how it searched", () => {
    renderWithQuery(
      <AnswerView
        answer={makeAnswer({
          abstained: true,
          abstain_reason: "question_not_covered",
          answer: "I can't answer that from the indexed documents.",
          citations: [],
        })}
        turnId={1}
      />,
    );
    expect(screen.getByText("No answer")).toBeInTheDocument();
    expect(screen.getByText(/doesn't appear anywhere in your documents/)).toBeInTheDocument();
    expect(screen.getByText(/How this was found/)).toBeInTheDocument();
  });
});
