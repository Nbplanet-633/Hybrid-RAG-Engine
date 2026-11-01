// Runs against the second server, started with ASKMYDOCS_API_KEY (see playwright.config.ts).
import { expect, test } from "@playwright/test";

import { E2E_API_KEY } from "./constants";
import { emptyLibrary } from "./fixtures";

const keyed = { "X-API-Key": E2E_API_KEY };

test.beforeEach(async ({ request }) => {
  await emptyLibrary(request, keyed);
});

test("the server's documents are not readable without the key", async ({ request }) => {
  expect((await request.get("/library/documents")).status()).toBe(401);
  // /library/info stays open, so the app can learn that it needs a key.
  const info = await request.get("/library/info");
  expect(await info.json()).toMatchObject({ requires_api_key: true });
});

test("the app asks for the key, refuses a wrong one, and opens with the right one", async ({
  page,
}) => {
  await page.goto("/");
  const keyInput = page.getByLabel("API key");
  await expect(keyInput).toBeVisible();
  await expect(page.getByLabel("Question")).toHaveCount(0);

  await keyInput.fill("not-the-key");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toContainText("didn't accept that key");

  await page.getByLabel("API key").fill(E2E_API_KEY);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Start by adding a document")).toBeVisible();

  // The key is sent with every request: loading samples is a keyed call.
  await page.getByRole("button", { name: "Try with sample documents" }).click();
  await expect(page.getByLabel("Search in")).toContainText("All documents (8)");
});

test("forgetting the key locks the app again", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("API key").fill(E2E_API_KEY);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Start by adding a document")).toBeVisible();

  await page.getByRole("button", { name: "Forget API key" }).click();

  await expect(page.getByLabel("API key")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0); // forgetting is not a rejection
});

test("an unremembered key lasts only for this tab", async ({ page, context }) => {
  await page.goto("/");
  await page.getByLabel("API key").fill(E2E_API_KEY);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Start by adding a document")).toBeVisible();

  const another = await context.newPage();
  await another.goto("/");
  await expect(another.getByLabel("API key")).toBeVisible();
});
