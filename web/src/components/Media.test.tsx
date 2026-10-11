import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, del, fileUrl, post } from "../lib/api";
import type { ImageModel, MediaItem } from "../lib/media";
import type { Me } from "../lib/types";
import { Media } from "./Media";

vi.mock("../lib/api", () => ({
  api: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
  fileUrl: vi.fn(() => Promise.resolve("blob:x")),
}));
vi.mock("../lib/events", () => ({ watchStream: () => ({ close: () => undefined }) }));

const me: Me = { sub: "p-ada", name: "Ada", username: "ada", owner: false, ownerReadsChats: false };
const flux: ImageModel = {
  id: "openrouter/flux",
  account: "openrouter",
  provider: "OpenRouter",
  locality: "external",
  ready: true,
  onDemand: false,
  maxImages: 1,
  qualities: [],
  backgrounds: [],
  outputFormats: ["png", "jpeg"],
  minReferences: 0,
  maxReferences: 4,
  edits: true,
};
const mini: ImageModel = {
  ...flux,
  id: "openrouter/mini",
  maxImages: 10,
  qualities: ["auto", "low", "high"],
  outputFormats: null,
};
const done: MediaItem = {
  id: "m1",
  door: "images",
  kind: "made",
  model: "openrouter/flux",
  request: { model: "openrouter/flux", prompt: "a red barn", size: "333x333" },
  status: "done",
  createdAt: 2,
  finishedAt: 3,
  served: { driver: "openrouter", latency_ms: 3500, attempts: 1 },
  units: { images: 1, sizes: ["1024x1024"] },
  text: null,
  error: null,
  files: [
    { id: "f1", name: "image-1.png", mediaType: "image/png", size: 9, width: 1024, height: 1024 },
  ],
};

function serve(models: ImageModel[], items: MediaItem[] = []) {
  vi.mocked(api).mockImplementation((path: string) =>
    Promise.resolve(
      path === "/api/media/doors"
        ? { doors: { images: { models } } }
        : { items, bytes: items.length * 9 },
    ),
  );
}

const show = (props: Partial<Parameters<typeof Media>[0]> = {}) =>
  render(
    <Media
      me={me}
      door="images"
      focus={null}
      person={null}
      onClose={() => undefined}
      onDoor={() => undefined}
      onToChat={() => undefined}
      onTextToChat={() => undefined}
      {...props}
    />,
  );

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

it("says why there is nothing to make images with, and who can fix it", async () => {
  serve([]);
  show();
  expect(await screen.findByText("No model here makes images yet.")).toBeInTheDocument();
  expect(screen.getByText("Ask the owner of this Workbench to add one.")).toBeInTheDocument();
  show({ me: { ...me, owner: true, consoleUrl: "https://hub" } });
  expect(await screen.findByRole("link", { name: "Open Eugene's console" })).toHaveAttribute(
    "href",
    "https://hub",
  );
});

it("picks no model for a first visit, then offers only what the chosen one lists", async () => {
  serve([flux, mini]);
  show();
  const picker = await screen.findByTestId("image-model");
  expect(picker).toHaveValue("");
  expect(screen.getByTestId("make-image")).toBeDisabled();
  fireEvent.change(picker, { target: { value: "openrouter/flux" } });
  expect(screen.getByText("This model makes one image at a time.")).toBeInTheDocument();
  expect(screen.queryByLabelText("Quality")).toBeNull();
  expect(screen.getByTestId("where-it-runs")).toHaveTextContent("Runs on OpenRouter");
  fireEvent.change(picker, { target: { value: "openrouter/mini" } });
  expect(screen.getByLabelText("How many")).toHaveAttribute("max", "10");
  expect(screen.getByLabelText("Quality")).toBeInTheDocument();
  // Format is null for mini: the backend checks it, so it is free text.
  expect(screen.getByText(/Checked by OpenRouter when sent/)).toBeInTheDocument();
});

it("sends the form and shows the result in the bin", async () => {
  serve([flux]);
  vi.mocked(post).mockResolvedValue({ ...done, status: "running", files: [] });
  show();
  fireEvent.change(await screen.findByTestId("image-model"), {
    target: { value: "openrouter/flux" },
  });
  fireEvent.change(screen.getByTestId("image-prompt"), { target: { value: "a red barn" } });
  fireEvent.click(screen.getByTestId("make-image"));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/media/images", {
      model: "openrouter/flux",
      prompt: "a red barn",
      size: "1024x1024",
    }),
  );
  expect(await screen.findByText(/keeps going/)).toBeInTheDocument();
});

it("shows what was asked beside what came back, and deletes only when confirmed", async () => {
  serve([flux], [done]);
  show();
  expect(await screen.findByTestId("size-words")).toHaveTextContent(
    "Asked 333 × 333, got 1024 × 1024",
  );
  fireEvent.click(screen.getByRole("button", { name: /^Delete/ }));
  expect(del).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^Delete/ }));
  await waitFor(() => expect(del).toHaveBeenCalledWith("/api/media/m1"));
});

it("marks the field a refusal named when the request is edited", async () => {
  const failed: MediaItem = {
    ...done,
    request: { model: "openrouter/flux", prompt: "a red barn", size: "1024x1024" },
    status: "failed",
    files: [],
    error: { message: "size: not taken", param: "size", status: 400 },
  };
  serve([flux], [failed]);
  show();
  expect(await screen.findByText(/the gateway named: size/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Edit and send/ }));
  expect(await screen.findByTestId("image-problem")).toHaveTextContent("size: not taken");
  expect(screen.getByRole("group", { name: "Shape" })).toHaveClass("border-error-line");
  expect(screen.getByTestId("image-prompt")).toHaveValue("a red barn");
});

it("shows a tab for each screen a model serves, and switches by address", async () => {
  vi.mocked(api).mockImplementation((path: string) =>
    Promise.resolve(
      path === "/api/media/doors"
        ? {
            doors: {
              images: { models: [flux] },
              speech: {
                models: [
                  { ...flux, id: "openrouter/kokoro", voices: ["af_heart"], formats: ["mp3"] },
                ],
              },
              transcription: { models: [] },
            },
          }
        : { items: [], bytes: 0 },
    ),
  );
  const onDoor = vi.fn();
  show({ door: "speech", onDoor });
  await screen.findByTestId("speech-model");
  const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
  expect(tabs).toEqual(["Images", "Speech"]);
  expect(screen.getByRole("tab", { name: "Speech" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("tab", { name: "Images" }));
  expect(onDoor).toHaveBeenCalledWith("images");
});

it("shows a transcript, says when the model heard less, and sends the text to a chat", async () => {
  const heard: MediaItem = {
    ...done,
    door: "transcription",
    model: "openrouter/whisper-turbo",
    request: { model: "openrouter/whisper-turbo", name: "note.mp3", clipSeconds: 3.07 },
    text: "The bench is ready.",
    units: { heardSeconds: 1.525, clipSeconds: 3.07 },
    files: [
      { id: "a1", name: "note.mp3", mediaType: "audio/mpeg", size: 9, width: null, height: null },
    ],
  };
  vi.mocked(api).mockImplementation((path: string) =>
    Promise.resolve(
      path === "/api/media/doors"
        ? {
            doors: {
              images: { models: [] },
              speech: { models: [] },
              transcription: { models: [] },
            },
          }
        : { items: [heard], bytes: 9 },
    ),
  );
  const onTextToChat = vi.fn();
  show({ door: "transcription", onTextToChat });
  expect(await screen.findByTestId("transcript")).toHaveTextContent("The bench is ready.");
  expect(screen.getByTestId("heard-words")).toHaveTextContent(
    "The model heard 1.5 s of this 3.1 s clip.",
  );
  expect(screen.getByTestId("heard-words")).toHaveClass("text-warn");
  expect(screen.queryByRole("button", { name: /Again/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Send to a chat/ }));
  expect(onTextToChat).toHaveBeenCalledWith("The bench is ready.");
  expect(screen.getByText("No model here turns speech into text yet.")).toBeInTheDocument();
});

it("shows someone else's bin read only, with no form or actions", async () => {
  serve([flux], [{ ...done, readOnly: true }]);
  show({ me: { ...me, owner: true }, person: { sub: "p-bo", name: "Bo" } });
  expect(await screen.findByText(/You are reading Bo's media/)).toBeInTheDocument();
  expect(api).toHaveBeenCalledWith("/api/people/p-bo/media?door=images");
  expect(screen.queryByTestId("image-model")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Send to a chat/ })).toBeNull();
  expect(screen.getByRole("button", { name: /Download/ })).toBeInTheDocument();
});

it("names the card on each bin action, and the confirm asks about that card", async () => {
  serve([flux], [done]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Delete: a red barn" }));
  expect(screen.getByRole("button", { name: "Keep: a red barn" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete for good: a red barn" }));
  await waitFor(() => expect(del).toHaveBeenCalledWith("/api/media/m1"));
});

it("names the card on the other bin actions too", async () => {
  serve([flux], [done]);
  show();
  expect(
    await screen.findByRole("button", { name: "Edit and send: a red barn" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Download: image-1.png, a red barn" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Use as reference: image-1.png, a red barn" }),
  ).toBeInTheDocument();
});

it("labels the transcript's copy button with its card", async () => {
  const heard: MediaItem = {
    ...done,
    door: "transcription",
    request: { model: "m", name: "note.mp3" },
    text: "The bench is ready.",
    files: [],
  };
  serve([], [heard]);
  show({ door: "transcription" });
  expect(
    await screen.findByRole("button", { name: "Copy transcript: Transcribed: note.mp3" }),
  ).toBeInTheDocument();
});

it("says the bin is loading until its first list, then that it is empty", async () => {
  let release: (v: unknown) => void = () => undefined;
  vi.mocked(api).mockImplementation((path: string) =>
    path === "/api/media/doors"
      ? Promise.resolve({ doors: { images: { models: [flux] } } })
      : new Promise((resolve) => (release = resolve)),
  );
  show();
  expect(await screen.findByRole("status")).toHaveTextContent("Loading…");
  expect(screen.queryByText(/Nothing here yet/)).toBeNull();
  release({ items: [], bytes: 0 });
  expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
  expect(screen.queryByText("Loading…")).toBeNull();
});

it("gives the count when emptying a bin, and says it cannot be undone", async () => {
  serve([flux], [done, { ...done, id: "m2" }]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Empty this bin" }));
  expect(
    screen.getByText("Delete all 2 results in this bin? This cannot be undone."),
  ).toBeInTheDocument();
});

it("says a failed download failed, naming the file and the cause", async () => {
  serve([flux], [done]);
  show();
  const button = await screen.findByRole("button", { name: /^Download/ });
  vi.mocked(fileUrl).mockRejectedValueOnce(new Error("The file is gone."));
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not download image-1.png: The file is gone.",
  );
});

it("names the file a bin image could not load", async () => {
  vi.mocked(fileUrl).mockRejectedValueOnce(new Error("Not found."));
  serve([flux], [done]);
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not load image-1.png: Not found.",
  );
});

it("follows the tabs pattern: one tab stop, arrows move and show, panel is linked", async () => {
  vi.mocked(api).mockImplementation((path: string) =>
    Promise.resolve(
      path === "/api/media/doors"
        ? {
            doors: {
              images: { models: [flux] },
              speech: {
                models: [
                  { ...flux, id: "openrouter/kokoro", voices: ["af_heart"], formats: ["mp3"] },
                ],
              },
              transcription: { models: [] },
            },
          }
        : { items: [], bytes: 0 },
    ),
  );
  const onDoor = vi.fn();
  show({ door: "images", onDoor });
  await screen.findByTestId("image-model");
  const images = screen.getByRole("tab", { name: "Images" });
  const speech = screen.getByRole("tab", { name: "Speech" });
  expect(images).toHaveAttribute("tabindex", "0");
  expect(speech).toHaveAttribute("tabindex", "-1");
  expect(images).toHaveAttribute("type", "button");
  expect(screen.getByRole("tabpanel")).toHaveAttribute("id", images.getAttribute("aria-controls"));
  expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Images");
  fireEvent.keyDown(images, { key: "ArrowRight" });
  expect(onDoor).toHaveBeenCalledWith("speech");
  expect(speech).toHaveFocus();
  fireEvent.keyDown(speech, { key: "Home" });
  expect(images).toHaveFocus();
});

/** The one heading of the page: App moves focus to it when the page opens, so it is a target and no stop of Tab. */
it("has one title, the page's heading, which App moves focus to when the page opens", async () => {
  serve([flux]);
  show();
  await screen.findByTestId("image-model");
  const title = screen.getByRole("heading", { level: 1, name: "Bins · Media" });
  expect(title).toHaveAttribute("id", "view-heading");
  expect(title).toHaveAttribute("tabindex", "-1");
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});

describe("a form that a refusal came back to", () => {
  // "Edit and send" scrolls the page back to the form. The page is its own
  // scroller (the window never scrolls); jsdom lays nothing out and has no
  // Element.scrollTo, so one is stood in for it.
  const scrolled = vi.fn();
  const windowScrolled = vi.fn();
  beforeEach(() => {
    scrolled.mockClear();
    windowScrolled.mockClear();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      value: scrolled,
      configurable: true,
    });
    vi.spyOn(window, "scrollTo").mockImplementation(windowScrolled);
  });
  afterEach(() => {
    delete (HTMLElement.prototype as { scrollTo?: unknown }).scrollTo;
    vi.restoreAllMocks();
  });

  const refused = (param: string, message: string): MediaItem => ({
    ...done,
    status: "failed",
    files: [],
    request: { model: "openrouter/mini", prompt: "a red barn", size: "1024x1024", n: 2 },
    model: "openrouter/mini",
    error: { message, param, status: 400 },
  });

  async function edit(item: MediaItem) {
    serve([flux, mini], [item]);
    show();
    fireEvent.click(await screen.findByRole("button", { name: /^Edit and send/ }));
    return screen.findByTestId("image-problem");
  }

  it("scrolls the page itself to the top, where the form is, and not the window, which never scrolls", async () => {
    await edit(refused("size", "size: not taken"));
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled).toHaveBeenCalledWith({ top: 0 });
    // The call is made on the page's own scroller: the section the whole screen is in.
    expect(scrolled.mock.contexts[0]).toBe(screen.getByRole("region", { name: "Bins · Media" }));
    expect(windowScrolled).not.toHaveBeenCalled();
  });

  it("begins the alert with the field's own label, so the alert says which field it is about", async () => {
    const alert = await edit(refused("size", "size: not taken"));
    expect(alert.textContent).toMatch(/^Shape/);
    expect(alert).toHaveTextContent("size: not taken");
    expect(alert).toHaveAttribute("role", "alert");
  });

  it("marks a text field with aria-invalid, the danger edge, and the alert as its description", async () => {
    const alert = await edit(refused("prompt", "prompt: too long"));
    expect(alert.textContent).toMatch(/^Describe the image/);
    const prompt = screen.getByTestId("image-prompt");
    expect(prompt).toHaveAttribute("aria-invalid", "true");
    expect(prompt).toHaveClass("border-error-line", "border-2");
    expect(prompt).toHaveAccessibleDescription(/prompt: too long/);
    // Nothing else is marked.
    expect(screen.getByTestId("image-model")).not.toHaveAttribute("aria-invalid");
    expect(screen.getByLabelText("Quality")).not.toHaveAttribute("aria-invalid");
  });

  it("marks the shape group, which has no aria-invalid, by its edge and by the alert that describes it", async () => {
    await edit(refused("size", "size: not taken"));
    const group = screen.getByRole("group", { name: "Shape" });
    expect(group).toHaveClass("border-error-line", "border-2", "group-invalid");
    expect(group).toHaveAccessibleDescription(/size: not taken/);
  });

  it("marks a choice, and the group of reference images", async () => {
    await edit(refused("quality", "quality: not offered"));
    const quality = screen.getByLabelText("Quality");
    expect(quality).toHaveAttribute("aria-invalid", "true");
    expect(quality).toHaveAccessibleDescription(/quality: not offered/);
  });

  it("names the reference images when the refusal was about an image it was sent", async () => {
    const alert = await edit(refused("image[0]", "image[0]: too large"));
    expect(alert.textContent).toMatch(/^Reference images/);
    const group = screen.getByRole("group", { name: "Reference images" });
    expect(group).toHaveClass("border-error-line", "group-invalid");
    expect(group).toHaveAccessibleDescription(/image\[0\]: too large/);
  });

  it("ties the choice that is free text to its help as well as to the alert", async () => {
    await edit(refused("output_format", "output_format: not accepted"));
    const format = screen.getByLabelText("Format");
    expect(format).toHaveAttribute("aria-invalid", "true");
    expect(format).toHaveAccessibleDescription(
      /Checked by OpenRouter when sent.*output_format: not accepted/s,
    );
  });

  it("takes the marks away when the form is sent again", async () => {
    const alert = await edit(refused("prompt", "prompt: too long"));
    expect(alert).toBeInTheDocument();
    vi.mocked(post).mockResolvedValue({ ...done, id: "m9", status: "running", files: [] });
    fireEvent.click(screen.getByTestId("make-image"));
    await waitFor(() => expect(screen.queryByTestId("image-problem")).toBeNull());
    expect(screen.getByTestId("image-prompt")).not.toHaveAttribute("aria-invalid");
  });
});

describe("the reason a button is disabled", () => {
  it("is the button's description, so a screen reader reads it, and goes when there is none", async () => {
    serve([flux]);
    show();
    const send = await screen.findByTestId("make-image");
    expect(send).toBeDisabled();
    expect(send).toHaveAccessibleDescription("Choose a model first.");
    fireEvent.change(screen.getByTestId("image-model"), { target: { value: "openrouter/flux" } });
    expect(send).toHaveAccessibleDescription("Describe the image first.");
    fireEvent.change(screen.getByTestId("image-prompt"), { target: { value: "a red barn" } });
    expect(send).toBeEnabled();
    expect(send).not.toHaveAccessibleDescription();
  });

  it("says the model's choices are checked by its backend when they are free text, under the field", async () => {
    serve([mini]);
    show();
    fireEvent.change(await screen.findByTestId("image-model"), {
      target: { value: "openrouter/mini" },
    });
    expect(screen.getByLabelText("Format")).toHaveAccessibleDescription(
      "Checked by OpenRouter when sent",
    );
  });
});

describe("the images form", () => {
  it("groups the reference images under their own label, once the model takes them", async () => {
    serve([flux]);
    show();
    fireEvent.change(await screen.findByTestId("image-model"), {
      target: { value: "openrouter/flux" },
    });
    const group = screen.getByRole("group", { name: "Reference images" });
    expect(group).toHaveTextContent("Optional: up to 4, from your bin or brought in.");
    expect(within(group).getByRole("button", { name: "Bring in an image" })).toBeInTheDocument();
  });

  it("names a reference image on the button that stops using it, and the button is a real target", async () => {
    serve([flux], [done]);
    show();
    fireEvent.change(await screen.findByTestId("image-model"), {
      target: { value: "openrouter/flux" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /^Use as reference: image-1.png/ }));
    const chip = await screen.findByTestId("references");
    expect(within(chip).getByText("image-1.png")).toBeInTheDocument();
    const stop = within(chip).getByRole("button", { name: "Stop using image-1.png" });
    expect(stop).toHaveClass("icon-btn");
    fireEvent.click(stop);
    expect(screen.queryByTestId("references")).toBeNull();
  });

  it("says in one line, under no field, that a model makes one image at a time", async () => {
    serve([flux]);
    show();
    fireEvent.change(await screen.findByTestId("image-model"), {
      target: { value: "openrouter/flux" },
    });
    expect(screen.getByText("This model makes one image at a time.")).toHaveClass("help");
    expect(screen.queryByLabelText("How many")).toBeNull();
  });

  it("puts the shapes in one named group, each shape a choice a keyboard reaches", async () => {
    serve([flux]);
    show();
    fireEvent.change(await screen.findByTestId("image-model"), {
      target: { value: "openrouter/flux" },
    });
    const group = screen.getByRole("group", { name: "Shape" });
    expect(within(group).getAllByRole("radio").length).toBeGreaterThanOrEqual(3);
    expect(within(group).getByRole("radio", { name: "Custom" })).not.toBeChecked();
    fireEvent.click(within(group).getByRole("radio", { name: "Custom" }));
    expect(within(group).getByLabelText("Custom size, width x height")).toBeInTheDocument();
  });
});

describe("deleting a result", () => {
  it("asks first: the safe answer has focus, Escape declines, and focus goes back to Delete", async () => {
    serve([flux], [done]);
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Delete: a red barn" }));
    const group = screen.getByRole("group", { name: "Delete this result?" });
    const keep = within(group).getByRole("button", { name: "Keep: a red barn" });
    expect(keep).toHaveFocus();
    expect(group).toHaveTextContent("Delete this result? This cannot be undone.");
    // The first Delete is not offered while the question is open.
    expect(screen.queryByRole("button", { name: "Delete: a red barn" })).toBeNull();
    fireEvent.keyDown(keep, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Delete this result?" })).toBeNull();
    expect(del).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Delete: a red barn" })).toHaveFocus(),
    );
  });

  it("says image, not result, for an image that was brought in", async () => {
    serve([flux], [{ ...done, kind: "upload" }]);
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Delete: Brought in: an image" }));
    expect(screen.getByRole("group", { name: "Delete this image?" })).toBeInTheDocument();
  });
});

describe("emptying a bin", () => {
  it("asks first: the safe answer has focus, Escape declines, and focus goes back to Empty this bin", async () => {
    serve([flux], [done, { ...done, id: "m2" }]);
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Empty this bin" }));
    const group = screen.getByRole("group", { name: "Empty this bin?" });
    const keep = within(group).getByRole("button", { name: "Keep them" });
    expect(keep).toHaveFocus();
    // Not offered again while the question is open.
    expect(screen.queryByRole("button", { name: "Empty this bin" })).toBeNull();
    fireEvent.keyDown(keep, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Empty this bin?" })).toBeNull();
    expect(del).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Empty this bin" })).toHaveFocus(),
    );
  });

  it("deletes every result in the bin only when the danger answer is taken", async () => {
    serve([flux], [done, { ...done, id: "m2" }]);
    vi.mocked(del).mockResolvedValue(undefined);
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Empty this bin" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete them all" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/media?door=images"));
  });

  it("is not offered to someone reading another person's bin", async () => {
    serve([flux], [done]);
    show({ me: { ...me, owner: true }, person: { sub: "p-bo", name: "Bo" } });
    await screen.findByText(/You are reading Bo's media/);
    expect(screen.queryByRole("button", { name: "Empty this bin" })).toBeNull();
  });
});

describe("what could not be done", () => {
  it("says what could not be read for a bin, in the words Workbench used", async () => {
    vi.mocked(api).mockImplementation((path: string) =>
      path === "/api/media/doors"
        ? Promise.resolve({ doors: { images: { models: [flux] } } })
        : Promise.reject(new Error("The media store is locked.")),
    );
    show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read this bin");
    expect(alert).toHaveTextContent("The media store is locked.");
    expect(screen.queryByText(/Nothing here yet/)).toBeNull();
  });

  it("says what could not be read for the list of models served", async () => {
    vi.mocked(api).mockImplementation((path: string) =>
      path === "/api/media/doors"
        ? Promise.reject(new Error("Workbench answered 502."))
        : Promise.resolve({ items: [], bytes: 0 }),
    );
    show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read which models are served");
    expect(alert).toHaveTextContent("Workbench answered 502.");
  });

  it("offers to read the bin again, and shows it when the second read works", async () => {
    let reads = 0;
    vi.mocked(api).mockImplementation((path: string) => {
      if (path === "/api/media/doors")
        return Promise.resolve({ doors: { images: { models: [flux] } } });
      reads += 1;
      return reads === 1
        ? Promise.reject(new Error("The media store is locked."))
        : Promise.resolve({ items: [done], bytes: 9 });
    });
    show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The media store is locked.");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("media-item")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("offers to read the list of models again, and shows the form when the second read works", async () => {
    let asked = 0;
    vi.mocked(api).mockImplementation((path: string) => {
      if (path !== "/api/media/doors") return Promise.resolve({ items: [], bytes: 0 });
      asked += 1;
      return asked === 1
        ? Promise.reject(new Error("Workbench answered 502."))
        : Promise.resolve({ doors: { images: { models: [flux] } } });
    });
    show();
    const alert = await screen.findByRole("alert");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("image-model")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says it is trying again while the read is out, and cannot be pressed twice", async () => {
    let reads = 0;
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(api).mockImplementation((path: string) => {
      if (path === "/api/media/doors")
        return Promise.resolve({ doors: { images: { models: [flux] } } });
      reads += 1;
      return reads === 1
        ? Promise.reject(new Error("The media store is locked."))
        : new Promise((resolve) => (finish = resolve));
    });
    show();
    fireEvent.click(
      within(await screen.findByRole("alert")).getByRole("button", { name: "Try again" }),
    );
    const waiting = await screen.findByRole("button", { name: "Trying again…" });
    expect(waiting).toBeDisabled();
    // The read that was asked for is the only one: the first failed, and this is the second.
    expect(reads).toBe(2);
    finish({ items: [], bytes: 0 });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("keeps an action's failure until it is dismissed, apart from the bin's own", async () => {
    serve([flux], [done]);
    vi.mocked(post).mockRejectedValue(new Error("That image is gone."));
    show();
    fireEvent.click(await screen.findByRole("button", { name: /^Use as reference: image-1.png/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Send to a chat: image-1.png/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That image is gone.");
    expect(alert).not.toHaveTextContent("Could not read");
    fireEvent.click(within(alert).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says a failed result failed, in an alert that keeps the field the gateway named", async () => {
    const failed: MediaItem = {
      ...done,
      status: "failed",
      files: [],
      error: { message: "size: not taken", param: "size", status: 400 },
    };
    serve([flux], [failed]);
    show();
    const status = await screen.findByTestId("media-status");
    expect(status).toHaveAttribute("role", "alert");
    expect(status).toHaveClass("notice", "notice--error");
    expect(status).toHaveTextContent("size: not taken (the gateway named: size)");
  });
});

describe("the page's frame", () => {
  it("sets the reading-only notice as a warning with words, and the tabs in one tab list", async () => {
    serve([flux], [done]);
    show({ me: { ...me, owner: true }, person: { sub: "p-bo", name: "Bo" } });
    const note = await screen.findByText(/You are reading Bo's media/);
    expect(note).toHaveClass("notice", "notice--warn");
    expect(note).toHaveAttribute("role", "status");
    expect(screen.getByRole("tablist", { name: "Media screens" })).toBeInTheDocument();
  });

  it("says Their bin to someone reading, and Your bin with the total to the owner of it", async () => {
    serve([flux], [done]);
    const first = show();
    expect(await screen.findByRole("heading", { name: "Your bin" })).toBeInTheDocument();
    expect(screen.getByTestId("bin-total")).toHaveTextContent("Your bins hold 9 bytes.");
    first.unmount();
    show({ me: { ...me, owner: true }, person: { sub: "p-bo", name: "Bo" } });
    expect(await screen.findByRole("heading", { name: "Their bin" })).toBeInTheDocument();
    expect(screen.queryByTestId("bin-total")).toBeNull();
  });

  it("marks the result that was asked for with the accent edge", async () => {
    serve([flux], [done, { ...done, id: "m2" }]);
    // jsdom lays nothing out and has no scrollIntoView, which the page uses to bring the result into view.
    const scrolled = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      value: scrolled,
      configurable: true,
    });
    try {
      show({ focus: "m2" });
      await screen.findAllByTestId("media-item");
      const items = screen.getAllByTestId("media-item");
      expect(items.find((i) => i.id === "media-m2")).toHaveClass("border-accent");
      expect(items.find((i) => i.id === "media-m1")).not.toHaveClass("border-accent");
      expect(scrolled).toHaveBeenCalledWith({ block: "center" });
    } finally {
      delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });
});
