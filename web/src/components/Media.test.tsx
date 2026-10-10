import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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
