import { addSamples, expect, test } from "./fixtures";

test.describe("theme and layout", () => {
  test("the theme choice survives a reload", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    const html = page.locator("html");
    await expect(html).not.toHaveClass(/dark/);

    // System -> Light -> Dark
    await page.getByRole("button", { name: /^System theme/ }).click();
    await page.getByRole("button", { name: /^Light theme/ }).click();
    await expect(html).toHaveClass(/dark/);

    await page.reload();
    // Applied by the inline script before React renders, so it is right at once.
    await expect(html).toHaveClass(/dark/);
    await expect(page.getByRole("button", { name: /^Dark theme/ })).toBeVisible();
  });

  test.describe("on a phone", () => {
    test.use({ viewport: { width: 375, height: 812 } });

    test("the library collapses so the question box comes first", async ({ page, request }) => {
      await addSamples(request);
      await page.goto("/");

      const toggle = page.getByRole("button", { name: "Your documents (8)" });
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(page.getByRole("button", { name: "Remove 01-getting-started.md" })).toBeHidden();
      await expect(page.getByLabel("Question")).toBeInViewport();

      await toggle.click();
      await expect(
        page.getByRole("button", { name: "Remove 01-getting-started.md" }),
      ).toBeVisible();
    });

    test("nothing overflows sideways", async ({ page, request }) => {
      await addSamples(request);
      await page.goto("/");
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBe(0);
    });
  });
});
