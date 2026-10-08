import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, del, post } from "../lib/api";
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
      onToChat={() => undefined}
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
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(del).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Edit and send" }));
  expect(await screen.findByTestId("image-problem")).toHaveTextContent("size: not taken");
  expect(screen.getByRole("group", { name: "Shape" })).toHaveClass("border-error-line");
  expect(screen.getByTestId("image-prompt")).toHaveValue("a red barn");
});

it("shows someone else's bin read only, with no form or actions", async () => {
  serve([flux], [{ ...done, readOnly: true }]);
  show({ me: { ...me, owner: true }, person: { sub: "p-bo", name: "Bo" } });
  expect(await screen.findByText(/You are reading Bo's images/)).toBeInTheDocument();
  expect(api).toHaveBeenCalledWith("/api/people/p-bo/media?door=images");
  expect(screen.queryByTestId("image-model")).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Send to a chat/ })).toBeNull();
  expect(screen.getByRole("button", { name: /Download/ })).toBeInTheDocument();
});
