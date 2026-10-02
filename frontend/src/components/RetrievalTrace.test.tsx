import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeAnswer } from "../test/utils";
import { RetrievalTrace } from "./RetrievalTrace";

describe("RetrievalTrace", () => {
  const answer = makeAnswer();

  it("lists every ranked passage with the leg that found it", () => {
    render(<RetrievalTrace passages={answer.retrieval!} citations={answer.citations} />);
    const rows = screen.getAllByRole("row").slice(1); // skip the header
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("Both")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Keywords")).toBeInTheDocument();
  });

  it("marks cited passages and shows a dash for a leg that missed", () => {
    render(<RetrievalTrace passages={answer.retrieval!} citations={answer.citations} />);
    const [first, second] = screen.getAllByRole("row").slice(1);
    expect(within(first!).getByText("S1")).toBeInTheDocument();
    expect(within(second!).queryByText("S1")).toBeNull();
    expect(within(second!).getByText("–")).toBeInTheDocument(); // no dense rank
  });

  it("shows filenames for both POSIX and Windows source paths", () => {
    render(<RetrievalTrace passages={answer.retrieval!} citations={[]} />);
    expect(screen.getByText("disputes.md")).toBeInTheDocument();
    expect(screen.getByText("fees.md")).toBeInTheDocument();
  });

  it("renders nothing when nothing was retrieved", () => {
    const { container } = render(<RetrievalTrace passages={[]} citations={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
