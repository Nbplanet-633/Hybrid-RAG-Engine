import { addSamples, expect, LEAVE_POLICY, markdownFile, test } from "./fixtures";

test.describe("document library", () => {
  test("an empty library invites an upload and can load the samples", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Start by adding a document")).toBeVisible();

    await page.getByRole("button", { name: "Try with sample documents" }).click();

    await expect(page.getByRole("button", { name: "Remove 01-getting-started.md" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Remove / })).toHaveCount(8);
    await expect(page.getByLabel("Search in")).toContainText("All documents (8)");
  });

  test("an uploaded file is indexed, listed, and searchable", async ({ page }) => {
    await page.goto("/");
    await page
      .getByLabel("Upload documents")
      .setInputFiles(markdownFile("leave-policy.md", LEAVE_POLICY));

    await expect(
      page.getByRole("listitem").filter({ hasText: "leave-policy.md" }).first(),
    ).toContainText("Added");
    await expect(page.getByRole("button", { name: "Remove leave-policy.md" })).toBeVisible();
    await expect(page.getByLabel("Search in").locator("option")).toContainText(["leave-policy.md"]);
  });

  test("bad files are refused with a reason, in the browser or by the server", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Upload documents").setInputFiles([
      // Rejected in the browser, before any upload: wrong type, and empty.
      { name: "photo.png", mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47]) },
      markdownFile("empty.md", ""),
      // Accepted by the browser, rejected by the server: it can't be parsed.
      { name: "broken.pdf", mimeType: "application/pdf", buffer: Buffer.from("not a pdf") },
    ]);

    const item = (name: string) => page.getByRole("listitem").filter({ hasText: name });
    await expect(item("photo.png")).toContainText("Unsupported file type");
    await expect(item("empty.md")).toContainText("This file is empty");
    await expect(item("broken.pdf")).toContainText("Could not read broken.pdf");
    await expect(page.getByText("No documents yet.")).toBeVisible();
  });

  test("deleting a document removes it everywhere", async ({ page, request }) => {
    await addSamples(request);
    await page.goto("/");

    await page.getByRole("button", { name: "Remove 04-webhooks.md" }).click();

    await expect(page.getByRole("button", { name: "Remove 04-webhooks.md" })).toHaveCount(0);
    await expect(page.getByLabel("Search in")).toContainText("All documents (7)");
    await expect(
      page.getByLabel("Search in").locator("option", { hasText: "04-webhooks.md" }),
    ).toHaveCount(0);
  });
});
