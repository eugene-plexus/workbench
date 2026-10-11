import { act, renderHook } from "@testing-library/react";

import {
  choose,
  decision,
  file,
  fresh,
  listing,
  MODEL,
  partial,
  scan,
  tooLong,
} from "../test-documents";
import { api, put } from "./api";
import {
  FIRST_POLL_MS,
  merge,
  POLL_MS,
  RETRY_MS,
  useChatFiles,
  useFileAnnouncements,
} from "./useChatFiles";
import type { ChatFile, ChatFiles } from "./types";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  api: vi.fn(),
  put: vi.fn(),
}));

/** An image: nothing to read and nothing to decide. */
const picture: ChatFile = { id: "i1", name: "shot.png", mediaType: "image/png", kind: "image" };

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api).mockReset();
  vi.mocked(put).mockReset();
});
afterEach(() => vi.useRealTimers());

/** Lets time pass, and every answer that was waiting arrive. */
const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const calls = () => vi.mocked(api).mock.calls.map(([path]) => path);

function open(model: string | null = "local-model", handed: ChatFile[] = []) {
  return renderHook(({ chosen }) => useChatFiles("c1", chosen, handed), {
    initialProps: { chosen: model },
  });
}

describe("watching files being read", () => {
  it("asks nothing while there is nothing to read", async () => {
    const view = open("local-model", [picture]);
    await wait(10_000);
    expect(api).not.toHaveBeenCalled();
    expect(view.result.current.files).toEqual([picture]);
  });

  it("asks soon after a document is attached, for the model that is picked", async () => {
    const view = open();
    vi.mocked(api).mockResolvedValue(listing([file()]));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS - 1);
    expect(api).not.toHaveBeenCalled();
    await wait(1);
    expect(calls()).toEqual(["/api/chats/c1/files?model=local-model"]);
  });

  it("shows what the server says of the file, and says what it knows of the model", async () => {
    const view = open();
    vi.mocked(api).mockResolvedValue(listing([file()]));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    expect(view.result.current.files[0]?.decision?.kind).toBe("whole");
    expect(view.result.current.facts).toEqual({
      model: MODEL,
      request: { used: 218, window: 32768, reserve: 1024, full: null },
    });
    expect(view.result.current.error).toBeNull();
  });

  it("asks again each second while it is still being read, and not again once it is read", async () => {
    const view = open();
    vi.mocked(api)
      .mockResolvedValueOnce(listing([fresh()]))
      .mockResolvedValueOnce(listing([fresh()]))
      .mockResolvedValue(listing([file()]));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.files[0]?.reading?.state).toBe("ready");
    // Nothing is being read: it is quiet, however long it waits.
    await wait(60_000);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("stops when the file is taken back while it is being read", async () => {
    const view = open();
    vi.mocked(api).mockResolvedValue(listing([fresh()]));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(1);
    act(() => view.result.current.drop("f1"));
    await wait(30_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("stops when the page goes", async () => {
    const view = open();
    vi.mocked(api).mockResolvedValue(listing([fresh()]));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    view.unmount();
    await wait(30_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("does not ask again when the page goes while an answer is on its way", async () => {
    const view = open();
    let answer!: (value: ChatFiles) => void;
    vi.mocked(api).mockReturnValue(new Promise((resolve) => (answer = resolve)));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => answer(listing([fresh()])));
    await wait(30_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("keeps asking while the file is read again because its text went missing", async () => {
    const view = open();
    const again = file({
      decision: decision({ kind: "pending", send: false, message: "Reading…" }),
    });
    vi.mocked(api).mockResolvedValue(listing([again]));
    act(() => view.result.current.add(again));
    await wait(FIRST_POLL_MS + POLL_MS * 2);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("says why it could not ask, keeps what it shows, and waits longer before it tries again", async () => {
    const view = open();
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 502."));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    expect(view.result.current.error).toBe("Workbench answered 502.");
    expect(view.result.current.files[0]?.decision?.kind).toBe("pending");
    await wait(RETRY_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    vi.mocked(api).mockResolvedValue(listing([file()]));
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    expect(view.result.current.error).toBeNull();
    expect(view.result.current.files[0]?.decision?.kind).toBe("whole");
  });

  it("takes an answer that is not a listing for no answer at all", async () => {
    const view = open();
    vi.mocked(api).mockResolvedValue({ chatId: "c1", sending: true } as never);
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    expect(view.result.current.facts).toBeNull();
    expect(view.result.current.error).toBeNull();
    expect(view.result.current.files[0]?.decision?.kind).toBe("pending");
    vi.mocked(api).mockResolvedValue(undefined as never);
    await wait(POLL_MS);
    expect(view.result.current.files).toHaveLength(1);
  });
});

describe("asking again for another model", () => {
  it("asks as soon as the model is changed, says it is checking, and then shows the new decisions", async () => {
    const view = open("local-model", [file()]);
    let answer!: (value: ChatFiles) => void;
    vi.mocked(api).mockReturnValue(new Promise((resolve) => (answer = resolve)));
    view.rerender({ chosen: "small" });
    expect(calls()).toEqual(["/api/chats/c1/files?model=small"]);
    expect(view.result.current.checking).toBe(true);
    await act(async () =>
      answer(listing([tooLong({ id: "f1" })], { model: { ...MODEL, id: "small", window: 4096 } })),
    );
    expect(view.result.current.checking).toBe(false);
    expect(view.result.current.files[0]?.decision?.kind).toBe("too_long");
    expect(view.result.current.facts?.model.id).toBe("small");
  });

  it("does not ask when there is no document to decide, or when the model is the same", async () => {
    const view = open("local-model", [picture]);
    view.rerender({ chosen: "small" });
    await wait(100);
    expect(api).not.toHaveBeenCalled();
    const docs = open("local-model", [file()]);
    vi.mocked(api).mockResolvedValue(listing([file()]));
    docs.rerender({ chosen: "local-model" });
    await wait(100);
    expect(api).not.toHaveBeenCalled();
  });

  it("asks for the chat's own model when none is picked, and for the one that is picked after", async () => {
    const view = open(null);
    vi.mocked(api).mockResolvedValue(listing([fresh()]));
    act(() => view.result.current.add(fresh()));
    await wait(FIRST_POLL_MS);
    expect(calls()).toEqual(["/api/chats/c1/files"]);
    view.rerender({ chosen: "a model/with a slash" });
    expect(calls()[1]).toBe("/api/chats/c1/files?model=a%20model%2Fwith%20a%20slash");
  });

  it("drops an answer that a later one overtook", async () => {
    const view = open("local-model", [file()]);
    const answers: ((value: ChatFiles) => void)[] = [];
    vi.mocked(api).mockImplementation(() => new Promise((resolve) => answers.push(resolve)));
    view.rerender({ chosen: "small" });
    view.rerender({ chosen: "other" });
    expect(answers).toHaveLength(2);
    // The later one answers first; the earlier, slower one must not put its words back.
    await act(async () => answers[1]!(listing([choose({ id: "f1" })])));
    expect(view.result.current.files[0]?.decision?.kind).toBe("choose");
    await act(async () => answers[0]!(listing([file()])));
    expect(view.result.current.files[0]?.decision?.kind).toBe("choose");
    expect(view.result.current.checking).toBe(false);
  });
});

describe("the files the page holds", () => {
  it("adds a file that was just uploaded, once, and a document is read at once", async () => {
    const view = open();
    act(() => view.result.current.add(picture));
    act(() => view.result.current.add(picture));
    expect(view.result.current.files).toEqual([picture]);
  });

  it("takes a file back, forgets what it was refused for, and asks again for the rest", async () => {
    const view = open("local-model", [file({ id: "a" }), file({ id: "b", name: "b.pdf" })]);
    vi.mocked(api).mockResolvedValue(listing([file({ id: "b", name: "b.pdf" })]));
    act(() =>
      view.result.current.refuse([{ id: "a", name: "report.pdf", decision: choose().decision! }]),
    );
    expect(view.result.current.problems.a).toBeDefined();
    vi.mocked(api).mockClear();
    act(() => view.result.current.drop("a"));
    expect(view.result.current.files.map((f) => f.id)).toEqual(["b"]);
    expect(view.result.current.problems.a).toBeUndefined();
    await wait(0);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("does not ask when the last document goes", async () => {
    const view = open("local-model", [file(), picture]);
    act(() => view.result.current.drop("f1"));
    await wait(5000);
    expect(api).not.toHaveBeenCalled();
    expect(view.result.current.files).toEqual([picture]);
  });

  it("lets go of everything once the message is sent", async () => {
    const view = open("local-model", [file()]);
    vi.mocked(api).mockResolvedValue(listing([file()]));
    await act(async () => view.result.current.reload());
    expect(view.result.current.facts).not.toBeNull();
    act(() => view.result.current.refuse([{ id: "f1", name: "report.pdf", decision: null }]));
    act(() => view.result.current.clear());
    expect(view.result.current.files).toEqual([]);
    expect(view.result.current.facts).toBeNull();
    expect(view.result.current.problems).toEqual({});
  });
});

describe("a file the server has and the page has not", () => {
  it("is added when the page first asks, because Send would carry it", async () => {
    const view = open();
    const forgotten = choose({ id: "old", name: "forgotten.txt" });
    vi.mocked(api).mockResolvedValue(listing([forgotten, fresh({ id: "new" })]));
    act(() => view.result.current.add(fresh({ id: "new" })));
    await wait(FIRST_POLL_MS);
    expect(view.result.current.files.map((f) => f.id)).toEqual(["new", "old"]);
  });

  it("is not added when it was sent, taken back, or sent just now", async () => {
    const view = open("local-model", [file({ id: "mine" })]);
    vi.mocked(api).mockResolvedValue(listing([file({ id: "mine" })]));
    // One that was sent from somewhere else, one this page took back, one it has just sent.
    act(() => view.result.current.add(file({ id: "gone" })));
    act(() => view.result.current.drop("gone"));
    act(() => view.result.current.add(file({ id: "away" })));
    act(() => view.result.current.clear());
    vi.mocked(api).mockResolvedValue(
      listing([
        file({ id: "elsewhere", sent: true }),
        file({ id: "gone" }),
        file({ id: "away" }),
        file({ id: "mine" }),
      ]),
    );
    await act(async () => view.result.current.reload());
    expect(view.result.current.files).toEqual([]);
  });
});

describe("merging what the server says", () => {
  const known = () => new Set<string>();

  it("keeps the page's own file when the server does not list it yet", () => {
    const mine = fresh();
    expect(merge([mine], [], known())).toEqual([mine]);
  });

  it("takes the server's version of a file, over the page's", () => {
    const merged = merge([fresh()], [file()], known());
    expect(merged).toHaveLength(1);
    expect(merged[0]?.decision?.kind).toBe("whole");
    expect(merged[0]?.reading?.state).toBe("ready");
  });

  it("lets go of a file the server says was sent", () => {
    expect(merge([file()], [file({ sent: true })], known())).toEqual([]);
  });

  it("adds an unsent file the page never held, once, and not one it has held", () => {
    const seen = new Set(["held"]);
    const merged = merge([], [file({ id: "held" }), file({ id: "new" })], seen);
    expect(merged.map((f) => f.id)).toEqual(["new"]);
    expect(seen.has("new")).toBe(true);
    expect(merge([], [file({ id: "new" })], seen)).toEqual([]);
  });
});

describe("choosing how much to read", () => {
  const mounted = () => open("local-model", [choose({ id: "f2" })]);

  it("sends the first part the offer names, keeps the server's answer, and asks again", async () => {
    const view = mounted();
    vi.mocked(put).mockResolvedValue(partial({ id: "f2" }));
    vi.mocked(api).mockResolvedValue(listing([partial({ id: "f2" })]));
    await act(async () =>
      view.result.current.choose(view.result.current.files[0]!, { mode: "first", tokens: 5859 }),
    );
    expect(put).toHaveBeenCalledExactlyOnceWith(
      "/api/chats/c1/files/f2/reading?model=local-model",
      {
        mode: "first",
        tokens: 5859,
      },
    );
    expect(view.result.current.files[0]?.decision?.kind).toBe("partial");
    expect(view.result.current.choosing).toBeNull();
    expect(calls()).toEqual(["/api/chats/c1/files?model=local-model"]);
  });

  it("shows the server's answer to the choice at once, before it asks again", async () => {
    const view = mounted();
    vi.mocked(put).mockResolvedValue(partial({ id: "f2" }));
    // The next read is slow: what is shown now is what the choice answered.
    vi.mocked(api).mockReturnValue(new Promise(() => undefined));
    act(() => {
      void view.result.current.choose(view.result.current.files[0]!, {
        mode: "first",
        tokens: 5859,
      });
    });
    await wait(0);
    expect(view.result.current.files[0]?.decision?.kind).toBe("partial");
    expect(view.result.current.files[0]?.reading?.chosen).toEqual({ mode: "first", tokens: 3000 });
  });

  it("sends 'read it all' as the whole, and takes a choice back with null", async () => {
    const view = mounted();
    vi.mocked(put).mockResolvedValue(choose({ id: "f2" }));
    vi.mocked(api).mockResolvedValue(listing([choose({ id: "f2" })]));
    await act(async () =>
      view.result.current.choose(view.result.current.files[0]!, { mode: "whole" }),
    );
    expect(vi.mocked(put).mock.calls[0]![1]).toEqual({ mode: "whole" });
    await act(async () => view.result.current.choose(view.result.current.files[0]!, null));
    expect(vi.mocked(put).mock.calls[1]![1]).toEqual({ mode: null });
  });

  it("says it is saving while it does", async () => {
    const view = mounted();
    let saved!: (value: ChatFile) => void;
    vi.mocked(put).mockReturnValue(new Promise((resolve) => (saved = resolve)));
    vi.mocked(api).mockResolvedValue(listing([partial({ id: "f2" })]));
    let done!: Promise<void>;
    act(() => {
      done = view.result.current.choose(view.result.current.files[0]!, { mode: "whole" });
    });
    expect(view.result.current.choosing).toBe("f2");
    await act(async () => {
      saved(partial({ id: "f2" }));
      await done;
    });
    expect(view.result.current.choosing).toBeNull();
  });

  it("is refused with the server's sentence, and leaves the file as it was", async () => {
    const view = mounted();
    const refusal = Object.assign(new Error("About 9,300 tokens."), {
      status: 409,
      detail: {
        message: "About 9,300 tokens. Choose how much to read.",
        code: "choice",
        files: [],
      },
    });
    vi.mocked(put).mockRejectedValue(refusal);
    vi.mocked(api).mockResolvedValue(listing([choose({ id: "f2" })]));
    await act(async () =>
      view.result.current.choose(view.result.current.files[0]!, { mode: "whole" }),
    );
    expect(view.result.current.problems.f2).toEqual({
      title: "Your choice was not kept",
      text: "About 9,300 tokens. Choose how much to read.",
    });
    expect(view.result.current.files[0]?.decision?.kind).toBe("choose");
    expect(view.result.current.choosing).toBeNull();
  });

  it("says what went wrong when it was not a refusal, and clears the sentence on the next try", async () => {
    const view = mounted();
    vi.mocked(put).mockRejectedValueOnce(new Error("Workbench answered 502."));
    vi.mocked(api).mockResolvedValue(listing([choose({ id: "f2" })]));
    await act(async () =>
      view.result.current.choose(view.result.current.files[0]!, { mode: "whole" }),
    );
    expect(view.result.current.problems.f2?.text).toBe("Workbench answered 502.");
    vi.mocked(put).mockResolvedValue(partial({ id: "f2" }));
    await act(async () =>
      view.result.current.choose(view.result.current.files[0]!, { mode: "whole" }),
    );
    expect(view.result.current.problems.f2).toBeUndefined();
  });
});

describe("a send the server refused for files", () => {
  it("puts each file's own words on its chip, takes the decisions the server made, and asks again", async () => {
    const view = open("local-model", [file({ id: "f2", name: "log.txt" })]);
    vi.mocked(api).mockResolvedValue(listing([choose({ id: "f2" })]));
    act(() =>
      view.result.current.refuse([{ id: "f2", name: "log.txt", decision: choose().decision! }]),
    );
    expect(view.result.current.files[0]?.decision?.kind).toBe("choose");
    expect(view.result.current.problems.f2).toEqual({
      title: "Your message was not sent",
      text: choose().decision!.message,
    });
    await wait(0);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("says a file that was still being read was only not read yet", () => {
    const view = open("local-model", [fresh({ id: "f1" })]);
    act(() =>
      view.result.current.refuse([{ id: "f1", name: "report.pdf", decision: fresh().decision! }]),
    );
    expect(view.result.current.problems.f1?.text).toBe(
      "This file was still being read. Try again in a moment.",
    );
    act(() => view.result.current.refuse([{ id: "f1", name: "report.pdf", decision: null }]));
    expect(view.result.current.problems.f1?.text).toBe(
      "This file was still being read. Try again in a moment.",
    );
  });

  it("is forgotten when the person deals with the file", () => {
    const view = open("local-model", [file()]);
    act(() =>
      view.result.current.refuse([{ id: "f1", name: "report.pdf", decision: scan().decision! }]),
    );
    expect(view.result.current.problems.f1).toBeDefined();
    act(() => view.result.current.dismiss("f1"));
    expect(view.result.current.problems).toEqual({});
  });
});

describe("what a screen reader is told as files change", () => {
  const read = (files: ChatFile[]) =>
    renderHook(({ list }) => useFileAnnouncements(list, MODEL), { initialProps: { list: files } });

  it("says nothing for a file that has only been attached and is being read", () => {
    expect(read([fresh()]).result.current).toBe("");
  });

  it("says what a file comes to once it has been read, and says it once", () => {
    const view = read([fresh()]);
    view.rerender({ list: [file()] });
    expect(view.result.current).toBe("report.pdf: Will be read whole.");
    view.rerender({ list: [file({ reading: { ...file().reading!, finishedAt: 5 } })] });
    expect(view.result.current).toBe("report.pdf: Will be read whole.");
  });

  it("says a file that stops fitting a model the person picked", () => {
    const view = read([file()]);
    expect(view.result.current).toBe("report.pdf: Will be read whole.");
    view.rerender({ list: [tooLong({ id: "f1", name: "report.pdf" })] });
    expect(view.result.current).toBe(
      "report.pdf: Too long for this model. This model's window is 4,096 tokens, so there is room for about 2,147 tokens of this file.",
    );
  });

  it("says every file that changed, in one breath, and nothing of an image", () => {
    const view = read([fresh(), fresh({ id: "f2", name: "log.txt" })]);
    view.rerender({ list: [file(), choose(), { id: "i", name: "a.png", mediaType: "image/png" }] });
    expect(view.result.current).toContain("report.pdf: Will be read whole.");
    expect(view.result.current).toContain("log.txt: Choose how much to read.");
    expect(view.result.current).not.toContain("a.png");
  });

  it("says a partial read that the person chose", () => {
    const view = read([choose()]);
    view.rerender({ list: [partial()] });
    expect(view.result.current).toBe(
      "log.txt: Will read lines 1 to 290 of 900 (about 2,997 tokens). The rest of the file is not sent.",
    );
  });
});
