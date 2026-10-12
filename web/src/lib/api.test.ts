import {
  ApiError,
  api,
  documentText,
  fileOf,
  fileText,
  problemOf,
  put,
  serverNow,
  SignedOut,
  upload,
} from "./api";
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

describe("the text Workbench read from a document", () => {
  const read = "[page 1]\nAnnual report.\n\n[page 3]\nThe code word is TAMARIND-6042.";

  it("is asked for by the file's id, with the request secret, and comes back as the text it is", async () => {
    window.localStorage.setItem(SECRET_KEY, "s3cret");
    const fetched = vi.fn<typeof fetch>(async () =>
      answer(200, read, { "Content-Type": "text/plain; charset=utf-8" }),
    );
    vi.stubGlobal("fetch", fetched);
    try {
      expect(await fileText("a/b c")).toBe(read);
      const [path, init] = fetched.mock.calls[0]!;
      expect(path).toBe("/api/files/a%2Fb%20c/text");
      expect(init?.credentials).toBe("same-origin");
      expect((init?.headers as Record<string, string>)[SECRET_HEADER]).toBe("s3cret");
    } finally {
      window.localStorage.removeItem(SECRET_KEY);
    }
  });

  it("says why it was refused, in the server's own words, for a file that is not there or not read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        answer(404, { detail: { message: "The text read from this file is no longer on disk." } }),
      ),
    );
    await expect(fileText("f")).rejects.toThrow(
      "The text read from this file is no longer on disk.",
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(409, { detail: { message: "This file is still being read." } })),
    );
    const failure = await failed(fileText("f"));
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(409);
  });

  it("is a sign-out when the person is not signed in", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(401, { detail: { message: "Sign in.", reason: "expired" } })),
    );
    await expect(fileText("f")).rejects.toBeInstanceOf(SignedOut);
  });
});

describe("the text of a saved document", () => {
  it("is asked for at the document's own address, by its id, with the request secret", async () => {
    window.localStorage.setItem(SECRET_KEY, "s3cret");
    const text = "[page 4]\nThe code word is TAMARIND-6042.";
    const fetched = vi.fn<typeof fetch>(async () =>
      answer(200, text, { "Content-Type": "text/plain; charset=utf-8" }),
    );
    vi.stubGlobal("fetch", fetched);
    try {
      expect(await documentText("a/b c")).toBe(text);
      const [path, init] = fetched.mock.calls[0]!;
      expect(path).toBe("/api/documents/a%2Fb%20c/text");
      expect(init?.credentials).toBe("same-origin");
      expect((init?.headers as Record<string, string>)[SECRET_HEADER]).toBe("s3cret");
    } finally {
      window.localStorage.removeItem(SECRET_KEY);
    }
  });

  it("says why it was refused, in the server's own words", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(409, { detail: { message: "This document is still being read." } })),
    );
    const failure = await failed(documentText("d"));
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(409);
    expect(failure.message).toBe("This document is still being read.");
  });

  it("is a sign-out when the person is not signed in", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(401, { detail: { message: "Sign in.", reason: "expired" } })),
    );
    await expect(documentText("d")).rejects.toBeInstanceOf(SignedOut);
  });
});

/** An XMLHttpRequest the test can answer, since jsdom's would try the network. */
class Sending {
  static last: Sending;
  method = "";
  url = "";
  sent: unknown = undefined;
  headers: Record<string, string> = {};
  aborted = false;
  status = 0;
  responseText = "";
  replyType: string | null = null;
  upload: {
    onprogress:
      ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null;
  } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() {
    Sending.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  getResponseHeader(name: string) {
    return name === "Content-Type" ? this.replyType : null;
  }
  send(body: unknown) {
    this.sent = body;
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
  answer(status: number, body: unknown, type: string | null = "application/json") {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.replyType = type;
    this.onload?.();
  }
}

describe("an upload", () => {
  const form = () => {
    const made = new FormData();
    made.append("files", new File(["x"], "a.pdf"), "a.pdf");
    return made;
  };
  beforeEach(() => vi.stubGlobal("XMLHttpRequest", Sending));

  it("posts the form to the path with the request secret, and leaves the form's type to the browser", async () => {
    window.localStorage.setItem(SECRET_KEY, "s3cret");
    try {
      const sent = form();
      const going = upload<{ documents: unknown[] }>("/api/documents", sent);
      const request = Sending.last;
      expect([request.method, request.url, request.sent]).toEqual(["POST", "/api/documents", sent]);
      expect(request.headers[SECRET_HEADER]).toBe("s3cret");
      expect(Object.keys(request.headers).map((name) => name.toLowerCase())).not.toContain(
        "content-type",
      );
      request.answer(201, { documents: [{ id: "d1" }], refused: [] });
      expect(await going).toEqual({ documents: [{ id: "d1" }], refused: [] });
    } finally {
      window.localStorage.removeItem(SECRET_KEY);
    }
  });

  it("says how much of the file has gone, and that all of it has when the answer comes", async () => {
    const seen: number[] = [];
    const going = upload("/api/documents", form(), { onProgress: (n) => seen.push(n) });
    const request = Sending.last;
    request.upload.onprogress?.({ lengthComputable: true, loaded: 25, total: 100 });
    request.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
    // A size it cannot know is not a fraction, and neither is a body with nothing in it.
    request.upload.onprogress?.({ lengthComputable: false, loaded: 60, total: 0 });
    request.upload.onprogress?.({ lengthComputable: true, loaded: 0, total: 0 });
    request.answer(201, { documents: [], refused: [] });
    await going;
    expect(seen).toEqual([0.25, 0.5, 1]);
  });

  it("is an answer with nothing in it when the server says there is nothing to say", async () => {
    const going = upload("/api/documents", form());
    Sending.last.answer(204, "");
    expect(await going).toBeUndefined();
  });

  it("reads an answer as JSON when the server did not say what it is", async () => {
    const going = upload<{ documents: unknown[] }>("/api/documents", form());
    Sending.last.answer(201, { documents: [{ id: "d1" }], refused: [] }, null);
    expect(await going).toEqual({ documents: [{ id: "d1" }], refused: [] });
    const refused = upload("/api/documents", form());
    Sending.last.answer(415, { detail: { message: "a.pdf is an image, not a document." } }, null);
    const failure = await failed(refused);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toBe("a.pdf is an image, not a document.");
  });

  it("reads a refusal as every other call does: the server's sentence and the fields that came with it", async () => {
    const going = upload("/api/documents", form());
    Sending.last.answer(415, {
      detail: {
        message: "a.pdf is an image, not a document.",
        refused: [{ name: "a.pdf", status: 415, message: "a.pdf is an image, not a document." }],
      },
    });
    const failure = await failed(going);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(415);
    expect(failure.message).toBe("a.pdf is an image, not a document.");
    expect(failure.detail?.refused).toHaveLength(1);
  });

  it("is a sign-out when the person is not signed in", async () => {
    const going = upload("/api/documents", form());
    Sending.last.answer(401, { detail: { message: "Sign in.", reason: "expired" } });
    await expect(going).rejects.toBeInstanceOf(SignedOut);
  });

  it("says the connection was lost when there is no answer at all", async () => {
    const going = upload("/api/documents", form());
    Sending.last.onerror?.();
    await expect(going).rejects.toThrow(
      "The connection to Workbench was lost while the file was being added.",
    );
  });

  it("stops when told to, and sends nothing for a person who had already said stop", async () => {
    const stop = new AbortController();
    const going = upload("/api/documents", form(), { signal: stop.signal });
    const request = Sending.last;
    stop.abort();
    expect(request.aborted).toBe(true);
    await expect(going).rejects.toMatchObject({ name: "AbortError" });
    const early = new AbortController();
    early.abort();
    await expect(upload("/api/documents", form(), { signal: early.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    // No request was even made for it.
    expect(Sending.last).toBe(request);
  });
});

describe("Workbench's clock", () => {
  const AT = Date.UTC(2026, 9, 11, 15, 0, 0);
  const said = (offsetSeconds: number) => new Date(AT + offsetSeconds * 1000).toUTCString();

  afterEach(() => vi.useRealTimers());

  async function read(headers: Record<string, string>, status = 200) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(status, status === 200 ? {} : { detail: "No." }, headers)),
    );
    await api("/api/documents/folders").catch(() => undefined);
  }

  it("is told by the Date header of an answer, so a browser whose clock is out says the right time", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(AT);
    await read({ Date: said(300) });
    // Workbench's clock is five minutes ahead of this browser's, and stays so as time passes.
    expect(serverNow()).toBeCloseTo(AT / 1000 + 300, 0);
    vi.setSystemTime(AT + 60_000);
    expect(serverNow()).toBeCloseTo(AT / 1000 + 360, 0);
    // The next answer corrects it: now Workbench says it is a minute ahead.
    await read({ Date: said(120) });
    expect(serverNow()).toBeCloseTo(AT / 1000 + 120, 0);
  });

  it("is kept when an answer has no Date header, or one that is not a date", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(AT);
    await read({ Date: said(-90) });
    expect(serverNow()).toBeCloseTo(AT / 1000 - 90, 0);
    await read({});
    expect(serverNow()).toBeCloseTo(AT / 1000 - 90, 0);
    await read({ Date: "yesterday, probably" });
    expect(serverNow()).toBeCloseTo(AT / 1000 - 90, 0);
  });

  it("is not told by a refusal, which is not an answer to what was asked", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(AT);
    await read({ Date: said(0) });
    await read({ Date: said(900) }, 500);
    expect(serverNow()).toBeCloseTo(AT / 1000, 0);
  });

  it("is the browser's own clock when Workbench's is the same", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(AT);
    await read({ Date: said(0) });
    expect(serverNow()).toBeCloseTo(Date.now() / 1000, 0);
  });
});
