import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { MediaItem, VideoModel } from "../lib/media";
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
/** grok-imagine-video as the gateway lists it (§6.4, measured 2026-10-08). */
const grok: VideoModel = {
  id: "x-ai/grok-imagine-video",
  account: "openrouter",
  provider: "OpenRouter",
  locality: "external",
  ready: true,
  onDemand: false,
  durations: [1, 2, 3, 12, 15],
  sizes: ["1280x720", "480x854", "854x480"],
  firstFrame: true,
  prices: [
    { sku: "cents_per_image_input", per: "input_image", usd: 0.002 },
    {
      sku: "cents_per_video_output_second_480p",
      per: "second",
      usd: 0.05,
      resolution: "480p",
      sizes: ["480x854", "854x480"],
    },
    {
      sku: "cents_per_video_output_second_720p",
      per: "second",
      usd: 0.07,
      resolution: "720p",
      sizes: ["1280x720"],
    },
  ],
};
const seedance: VideoModel = {
  ...grok,
  id: "bytedance/seedance",
  durations: [4, 5],
  sizes: null,
  firstFrame: false,
  prices: null,
};
const running: MediaItem = {
  id: "v1",
  door: "video",
  kind: "made",
  model: grok.id,
  request: { model: grok.id, prompt: "a red ball", seconds: 1, size: "854x480" },
  status: "running",
  createdAt: Date.now() / 1000 - 18,
  finishedAt: null,
  served: { driver: "openrouter", latency_ms: 3250, attempts: 1 },
  units: null,
  text: null,
  error: null,
  job: { status: "queued", progress: 0, polls: 3, polledAt: 0, problem: null },
  files: [],
};

function serve(models: VideoModel[], items: MediaItem[] = []) {
  vi.mocked(api).mockImplementation((path: string) =>
    Promise.resolve(
      path === "/api/media/doors"
        ? { doors: { video: { models } } }
        : { items: path.includes("door=video") ? items : [], bytes: 0 },
    ),
  );
}

const show = () =>
  render(
    <Media
      me={me}
      door="video"
      focus={null}
      person={null}
      onClose={() => undefined}
      onDoor={() => undefined}
      onToChat={() => undefined}
      onTextToChat={() => undefined}
    />,
  );

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

it("says why there is nothing to make videos with", async () => {
  serve([]);
  show();
  expect(await screen.findByText("No model here makes videos yet.")).toBeInTheDocument();
  expect(
    screen.getByText("Video models that run on your own machines are coming later."),
  ).toBeInTheDocument();
});

it("asks first, with the price from the listing, and sends only when told to", async () => {
  serve([grok, seedance]);
  vi.mocked(post).mockResolvedValue(running);
  show();
  const picker = await screen.findByTestId("video-model");
  expect(picker).toHaveValue("");
  fireEvent.change(picker, { target: { value: grok.id } });
  // The least the model asks: its shortest length and its smallest size.
  expect(screen.getByTestId("video-seconds")).toHaveValue("1");
  expect(screen.getByTestId("video-size")).toHaveValue("854x480");
  fireEvent.change(screen.getByTestId("video-prompt"), { target: { value: "a red ball" } });
  fireEvent.change(screen.getByTestId("video-seconds"), { target: { value: "12" } });
  fireEvent.click(screen.getByTestId("make-video"));
  expect(screen.getByTestId("video-quote")).toHaveTextContent(
    "12 s at 480p. About $0.60, billed to OpenRouter.",
  );
  expect(post).not.toHaveBeenCalled();
  // A change asks again, so the price asked about is what is sent.
  fireEvent.change(screen.getByTestId("video-size"), { target: { value: "1280x720" } });
  expect(screen.queryByTestId("video-quote")).toBeNull();
  fireEvent.click(screen.getByTestId("make-video"));
  expect(screen.getByTestId("video-quote")).toHaveTextContent("12 s at 720p. About $0.84");
  fireEvent.click(screen.getByTestId("confirm-video"));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/media/video", {
      model: grok.id,
      prompt: "a red ball",
      seconds: 12,
      size: "1280x720",
    }),
  );
});

it("says when no price is listed, never that it is free", async () => {
  serve([seedance]);
  show();
  fireEvent.change(await screen.findByTestId("video-model"), { target: { value: seedance.id } });
  expect(screen.getByText("This model chooses its own size.")).toBeInTheDocument();
  expect(screen.queryByTestId("video-frame")).toBeNull();
  fireEvent.change(screen.getByTestId("video-prompt"), { target: { value: "waves" } });
  fireEvent.click(screen.getByTestId("make-video"));
  expect(screen.getByTestId("video-quote")).toHaveTextContent(
    "4 s, at the model's own size. No price is listed for this; OpenRouter bills it to that account.",
  );
});

it("lists long jobs with how long each has run, and what a finished one cost", async () => {
  const finished: MediaItem = {
    ...running,
    id: "v2",
    status: "done",
    units: { seconds: 1, size: "854x480", costUsd: 0.05 },
    job: null,
    files: [
      { id: "f1", name: "video.mp4", mediaType: "video/mp4", size: 9, width: null, height: null },
    ],
  };
  const stopped: MediaItem = { ...running, id: "v3", status: "stopped", job: null };
  serve([grok], [running, finished, stopped]);
  show();
  expect(
    await screen.findByRole("heading", { name: "Work orders · Long jobs" }),
  ).toBeInTheDocument();
  const statuses = await screen.findAllByTestId("media-status");
  expect(statuses[0]).toHaveTextContent(/^Working, 1[89] s so far\./);
  expect(screen.getByTestId("billed-words")).toHaveTextContent(
    "The provider billed $0.05 for this.",
  );
  expect(await screen.findByTestId("bin-video")).toBeInTheDocument();
  expect(statuses[1]).toHaveTextContent("Eugene cannot cancel a video job");
  // A video is sent again only through the form, which asks first.
  expect(screen.queryByRole("button", { name: /Again/ })).toBeNull();
  expect(screen.getAllByRole("button", { name: /^Edit and send/ })).toHaveLength(2);
  expect(screen.queryByRole("button", { name: /Send to a chat/ })).toBeNull();
});

it("moves focus to the safe answer when the price appears, and back when declined", async () => {
  serve([grok]);
  show();
  fireEvent.change(await screen.findByTestId("video-model"), { target: { value: grok.id } });
  fireEvent.change(screen.getByTestId("video-prompt"), { target: { value: "a red ball" } });
  fireEvent.click(screen.getByTestId("make-video"));
  const notYet = screen.getByRole("button", { name: "Not yet" });
  expect(notYet).toHaveFocus();
  fireEvent.click(notYet);
  expect(screen.getByTestId("make-video")).toHaveFocus();
});

it("asks about the price in a warning, with one primary action, and takes the form's own button away meanwhile", async () => {
  serve([grok]);
  show();
  fireEvent.change(await screen.findByTestId("video-model"), { target: { value: grok.id } });
  fireEvent.change(screen.getByTestId("video-prompt"), { target: { value: "a red ball" } });
  fireEvent.click(screen.getByTestId("make-video"));
  const group = screen.getByRole("group", { name: "Before sending" });
  expect(group).toHaveClass("notice", "notice--warn");
  expect(within(group).getByTestId("video-quote")).toHaveTextContent(
    "1 s at 480p. About $0.05, billed to OpenRouter.",
  );
  expect(within(group).getByTestId("confirm-video")).toHaveClass("btn-primary");
  expect(within(group).getByRole("button", { name: "Not yet" })).not.toHaveClass("btn-primary");
  expect(screen.queryByTestId("make-video")).toBeNull();
});

it("says why Make the video cannot be pressed yet, as the button's own description", async () => {
  serve([grok]);
  show();
  const make = await screen.findByTestId("make-video");
  expect(make).toBeDisabled();
  expect(make).toHaveAccessibleDescription("Choose a model first.");
  fireEvent.change(screen.getByTestId("video-model"), { target: { value: grok.id } });
  expect(make).toHaveAccessibleDescription("Describe the video first.");
  fireEvent.change(screen.getByTestId("video-prompt"), { target: { value: "a red ball" } });
  expect(make).toBeEnabled();
  expect(make).not.toHaveAccessibleDescription();
});

it("labels the length and size, and the first frame as optional, and offers to bring one in", async () => {
  serve([grok]);
  show();
  fireEvent.change(await screen.findByTestId("video-model"), { target: { value: grok.id } });
  expect(screen.getByLabelText("Length")).toBe(screen.getByTestId("video-seconds"));
  expect(screen.getByLabelText("Size")).toBe(screen.getByTestId("video-size"));
  expect(screen.getByLabelText("First frame (optional)")).toBe(screen.getByTestId("video-frame"));
  expect(screen.getByRole("button", { name: "Bring in an image" })).toBeInTheDocument();
});

it("says a length the backend checks is free, and under the box who checks it", async () => {
  serve([{ ...seedance, durations: null }]);
  show();
  fireEvent.change(await screen.findByTestId("video-model"), { target: { value: seedance.id } });
  const length = screen.getByLabelText("Length");
  expect(length.tagName).toBe("INPUT");
  expect(length).toHaveAccessibleDescription("Checked by OpenRouter when sent");
});

describe("a form that a refusal came back to", () => {
  // "Edit and send" scrolls the page (its own scroller) back to the form;
  // jsdom has no Element.scrollTo, so there the call does nothing (Media.test
  // holds that it is made).
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const refused = (param: string, message: string): MediaItem => ({
    ...running,
    id: "v9",
    status: "failed",
    job: null,
    error: { message, param, status: 400 },
  });

  async function edit(param: string, message: string) {
    serve([grok], [refused(param, message)]);
    show();
    fireEvent.click(await screen.findByRole("button", { name: /^Edit and send/ }));
    return screen.findByTestId("video-problem");
  }

  it("begins the alert with the field's own label, and marks the field with the alert as its description", async () => {
    const alert = await edit("seconds", "seconds: not one of the lengths offered");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert.textContent).toMatch(/^Length/);
    const seconds = screen.getByTestId("video-seconds");
    expect(seconds).toHaveAttribute("aria-invalid", "true");
    expect(seconds).toHaveClass("border-error-line", "border-2");
    expect(seconds).toHaveAccessibleDescription(/not one of the lengths offered/);
    expect(screen.getByTestId("video-size")).not.toHaveAttribute("aria-invalid");
    expect(screen.getByTestId("video-prompt")).not.toHaveAttribute("aria-invalid");
  });

  it.each([
    ["prompt", "Describe the video", "video-prompt"],
    ["size", "Size", "video-size"],
    ["input_reference", "First frame", "video-frame"],
  ])("marks %s, and names it %s", async (param, label, testId) => {
    const alert = await edit(param, `${param}: refused`);
    expect(alert.textContent).toMatch(new RegExp(`^${label}`));
    expect(screen.getByTestId(testId)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId(testId)).toHaveAccessibleDescription(/refused/);
  });

  it("takes the marks away when the form is sent again", async () => {
    await edit("prompt", "prompt: refused");
    vi.mocked(post).mockResolvedValue(running);
    fireEvent.click(screen.getByTestId("make-video"));
    fireEvent.click(screen.getByTestId("confirm-video"));
    await waitFor(() => expect(screen.queryByTestId("video-problem")).toBeNull());
    expect(screen.getByTestId("video-prompt")).not.toHaveAttribute("aria-invalid");
  });
});

describe("a long job", () => {
  it("says in an alert that a job failed, with what the gateway said, apart from running ones", async () => {
    const failed: MediaItem = {
      ...running,
      id: "v8",
      status: "failed",
      job: null,
      error: { message: "The provider refused this prompt.", param: null, status: 400 },
    };
    serve([grok], [running, failed]);
    show();
    const statuses = await screen.findAllByTestId("media-status");
    const alert = statuses.find((s) => s.getAttribute("role") === "alert");
    expect(alert).toBeDefined();
    expect(alert).toHaveTextContent("The provider refused this prompt.");
    expect(alert).toHaveClass("notice--error");
    expect(statuses.filter((s) => s.getAttribute("role") === "alert")).toHaveLength(1);
  });
});
