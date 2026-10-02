import { describe, expect, it, vi } from "vitest";

import { stubFetch } from "../test/utils";
import { api, ApiError } from "./client";

describe("api client", () => {
  it("returns parsed JSON on success", async () => {
    stubFetch(() => [200, [{ doc_id: "a", filename: "a.md" }]]);
    await expect(api.documents()).resolves.toEqual([{ doc_id: "a", filename: "a.md" }]);
  });

  it("surfaces the server's own error message", async () => {
    stubFetch(() => [409, { detail: "The library is empty. POST /library/documents first." }]);
    const error = await api.ask({ question: "hi" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, message: expect.stringContaining("empty") });
  });

  it("joins FastAPI validation errors into one message", async () => {
    stubFetch(() => [
      422,
      { detail: [{ msg: "String should have at least 1 character" }, { msg: "Too long" }] },
    ]);
    await expect(api.ask({ question: "" })).rejects.toThrow(
      "String should have at least 1 character; Too long",
    );
  });

  it("explains an unreachable server instead of a raw network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.info()).rejects.toThrow(/Can't reach the server/);
  });

  it("treats 204 No Content as success", async () => {
    stubFetch(() => [204, null]);
    await expect(api.deleteDocument("doc")).resolves.toBeUndefined();
  });

  it("encodes ids that end up in the URL path", async () => {
    const fetch = stubFetch(() => [204, null]);
    await api.deleteDocument("a/b c");
    expect(fetch.mock.calls[0]?.[0]).toBe("/library/documents/a%2Fb%20c");
  });
});
