import { test as base, expect, type APIRequestContext, type Page } from "@playwright/test";

/** Remove every document, so each test starts from an empty library. */
export async function emptyLibrary(
  request: APIRequestContext,
  headers: Record<string, string> = {},
) {
  const response = await request.get("/library/documents", { headers });
  expect(response.ok()).toBeTruthy();
  for (const doc of (await response.json()) as { doc_id: string }[]) {
    const deleted = await request.delete(`/library/documents/${encodeURIComponent(doc.doc_id)}`, {
      headers,
    });
    expect(deleted.status()).toBe(204);
  }
}

/** Load the bundled sample corpus through the API (8 Markdown files). */
export async function addSamples(request: APIRequestContext) {
  const response = await request.post("/library/samples");
  expect(response.ok()).toBeTruthy();
}

/** Type a question and wait until its answer (or refusal) has rendered. */
export async function ask(page: Page, question: string) {
  await page.getByLabel("Question").fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const turn = page.getByRole("article").filter({ hasText: question }).first();
  await expect(turn).toHaveAttribute("aria-busy", "false");
  return turn;
}

/** A Markdown file built in memory, for the upload input. */
export function markdownFile(name: string, body: string) {
  return { name, mimeType: "text/markdown", buffer: Buffer.from(body, "utf8") };
}

export const LEAVE_POLICY = `# Leave Policy

## Notice period

The notice period for resignation is 60 days for permanent staff.

## Annual leave

Employees receive 24 days of annual leave per calendar year.
`;

/** Every app test begins with an empty library, whatever the previous test left. */
export const test = base.extend<{ cleanLibrary: void }>({
  cleanLibrary: [
    // Named `provide`, not `use`: eslint-plugin-react-hooks treats `use` as a React hook.
    async ({ request }, provide) => {
      await emptyLibrary(request);
      await provide();
    },
    { auto: true },
  ],
});

export { expect };
