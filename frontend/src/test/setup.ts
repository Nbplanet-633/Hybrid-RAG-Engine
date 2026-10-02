import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// jsdom doesn't implement layout, so scrolling is a no-op the components can call.
Element.prototype.scrollIntoView = vi.fn();
