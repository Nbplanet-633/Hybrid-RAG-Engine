import { addSamples, ask, expect, test } from "./fixtures";

test.describe("asking questions", () => {
  test.beforeEach(async ({ page, request }) => {
    await addSamples(request);
    await page.goto("/");
  });

  test("an answer cites its source, and the citation quotes the answer's own sentence", async ({
    page,
  }) => {
    const turn = await ask(page, "How long do I have to submit dispute evidence?");

    await expect(turn).toContainText("7 calendar days");
    const source = turn.getByRole("listitem").filter({ hasText: "03-refunds-and-disputes.md" });
    await expect(source).toBeVisible();
    await expect(source.locator("blockquote")).toContainText("7 calendar days");
  });

  test("clicking a citation marker highlights its source", async ({ page }) => {
    const turn = await ask(page, "What is the dispute fee?");

    await turn.getByRole("button", { name: "Go to source 1" }).click();

    await expect(turn.locator("[id$='-S1']")).toHaveClass(/ring-2/);
  });

  test("a source opens to its full passage with the quote marked", async ({ page }) => {
    const turn = await ask(page, "What is the dispute fee?");

    await turn.getByRole("button", { name: "Show full passage" }).click();

    await expect(turn.locator("mark")).toContainText("€15.00 dispute fee");
    await expect(turn.getByRole("button", { name: "Show quote only" })).toBeVisible();
  });

  test("the retrieval trace shows how each passage was found", async ({ page }) => {
    const turn = await ask(page, "What is the dispute fee?");

    await turn.getByText("How this was found").click();

    const rows = turn.locator("tbody tr");
    await expect(rows.first()).toBeVisible();
    await expect(rows.first()).toContainText(/Both|Meaning|Keywords/);
    // The cited passage carries its marker in the trace too.
    await expect(rows.filter({ hasText: "S1" })).toHaveCount(1);
  });

  test("an out-of-scope question is refused, with the reason", async ({ page }) => {
    const turn = await ask(page, "What is the capital of France?");

    await expect(turn).toContainText("No answer");
    await expect(turn).toContainText("deliberate");
  });

  test("scoping to one document keeps other documents out of the answer", async ({ page }) => {
    await page.getByLabel("Search in").selectOption({ label: "08-sdks-and-support.md" });

    const turn = await ask(page, "How long do I have to submit dispute evidence?");

    await expect(turn).toContainText("Searched: 08-sdks-and-support.md");
    await expect(turn).toContainText("No answer");
  });

  test("earlier questions stay on screen, newest first, until cleared", async ({ page }) => {
    await ask(page, "What is the dispute fee?");
    await ask(page, "How long do I have to submit dispute evidence?");

    const questions = page.getByRole("article").locator("header p").first();
    await expect(page.getByRole("article")).toHaveCount(2);
    await expect(questions).toHaveText("How long do I have to submit dispute evidence?");
    await expect(page.getByLabel("Question")).toBeFocused();

    await page.getByRole("button", { name: "Clear", exact: true }).click();
    await expect(page.getByRole("article")).toHaveCount(0);
  });
});
