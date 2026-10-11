import { ApiError, api, fileOf, problemOf, put } from "./api";
import { SECRET_HEADER, SECRET_KEY } from "./session";

function answer(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers });
}

afterEach(() => vi.unstubAllGlobals());

/** The error a call ended in; a call that did not fail is itself a failure. */
async function failed(work: Promise<unknown>): Promise<ApiError> {
  try {
    await work;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("It did not fail.");
}

describe("a refusal", () => {
  it("keeps the server's sentence, and the fields that came with it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        answer(422, {
          detail: {
            message: "That looks like a password or key, so Workbench will not keep it",
            reason: "secret",
            looksLike: "a card number",
          },
        }),
      ),
    );
    const failure = await failed(api("/api/memory/notes", { method: "POST", body: "{}" }));
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe(
      "That looks like a password or key, so Workbench will not keep it",
    );
    expect(failure.status).toBe(422);
    expect(failure.detail).toEqual({
      message: "That looks like a password or key, so Workbench will not keep it",
      reason: "secret",
      looksLike: "a card number",
    });
  });

  it("is still just a sentence when that is all the server said", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(404, { detail: "Not Found" })),
    );
    const failure = await failed(api("/api/nope"));
    expect(failure.message).toBe("Not Found");
    expect(failure.detail).toBeUndefined();
  });

  it("falls back to the status for a body that is not one, or the stock list of a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(502, "<html>Bad gateway</html>")),
    );
    expect((await failed(api("/api/x"))).message).toBe("Workbench answered 502.");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(422, { detail: [{ loc: ["body", "text"], msg: "too long" }] })),
    );
    const list = await failed(api("/api/x"));
    expect(list.message).toBe("Workbench answered 422.");
    expect(list.detail).toBeUndefined();
  });

  it("is read the same by problemOf", async () => {
    expect(
      await problemOf(answer(409, { detail: { message: "You have 300 notes.", reason: "full" } })),
    ).toBe("You have 300 notes.");
  });
});

describe("put", () => {
  it("sends its body as JSON with the verb PUT", async () => {
    const fetched = vi.fn<typeof fetch>(async () => answer(200, { enabled: false }));
    vi.stubGlobal("fetch", fetched);
    expect(await put("/api/memory/prefs", { enabled: false })).toEqual({ enabled: false });
    const [path, init] = fetched.mock.calls[0]!;
    expect(path).toBe("/api/memory/prefs");
    expect(init?.method).toBe("PUT");
    expect(init?.body).toBe('{"enabled":false}');
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
});

describe("a file the server makes on request", () => {
  it("comes back as a blob with the name the server gave it", async () => {
    const fetched = vi.fn<typeof fetch>(async () =>
      answer(200, "# Memory", {
        "Content-Disposition": 'attachment; filename="workbench-memory.md"',
      }),
    );
    vi.stubGlobal("fetch", fetched);
    const file = await fileOf("/api/memory/export?format=md");
    expect(file.name).toBe("workbench-memory.md");
    expect(await file.blob.text()).toBe("# Memory");
    expect(fetched.mock.calls[0]![0]).toBe("/api/memory/export?format=md");
    expect(fetched.mock.calls[0]![1]?.credentials).toBe("same-origin");
  });

  it("is asked for with the request secret, since a plain link cannot carry it", async () => {
    window.localStorage.setItem(SECRET_KEY, "s3cret");
    const fetched = vi.fn<typeof fetch>(async () => answer(200, "{}"));
    vi.stubGlobal("fetch", fetched);
    try {
      await fileOf("/api/memory/export?format=json");
      expect((fetched.mock.calls[0]![1]?.headers as Record<string, string>)[SECRET_HEADER]).toBe(
        "s3cret",
      );
    } finally {
      window.localStorage.removeItem(SECRET_KEY);
    }
  });

  it("has no name when the server gave none, and says why when it was refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(200, "{}")),
    );
    expect((await fileOf("/api/x")).name).toBeNull();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(500, { detail: { message: "The disk is read only." } })),
    );
    await expect(fileOf("/api/x")).rejects.toThrow("The disk is read only.");
  });
});
