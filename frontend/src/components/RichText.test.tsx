import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RichText } from "./RichText";

describe("RichText", () => {
  it("renders bold and inline code", () => {
    const { container } = render(<RichText text="Use **the API** with `curl` today" />);
    expect(container.querySelector("strong")).toHaveTextContent("the API");
    expect(container.querySelector("code")).toHaveTextContent("curl");
    expect(container).toHaveTextContent("Use the API with curl today");
  });

  it("parses code nested inside bold", () => {
    // The extractive generator bolds whole sentences that contain code spans.
    const { container } = render(<RichText text="**Wait for the `dispute.created` webhook.**" />);
    expect(container.querySelector("strong code")).toHaveTextContent("dispute.created");
    expect(container.textContent).not.toContain("`");
  });

  it("turns citation markers into buttons that report their marker", async () => {
    const onMarker = vi.fn();
    render(<RichText text="Seven days [S1] or ten [S2]." onMarker={onMarker} />);
    await userEvent.click(screen.getByRole("button", { name: "Go to source 2" }));
    expect(onMarker).toHaveBeenCalledWith("S2");
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("leaves markers as plain text without a handler", () => {
    const { container } = render(<RichText text="Seven days [S1]." />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(container).toHaveTextContent("Seven days [S1].");
  });

  it("never renders document text as HTML", () => {
    // Uploaded documents are untrusted; markup in them must stay inert text.
    const hostile = '<img src=x onerror="alert(1)"> **<script>bad()</script>**';
    const { container } = render(<RichText text={hostile} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container).toHaveTextContent('<img src=x onerror="alert(1)">');
  });
});
