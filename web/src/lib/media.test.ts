import {
  fieldOf,
  groupModels,
  type ImageModel,
  imageBody,
  maxImages,
  mediaFromPath,
  type MediaItem,
  offer,
  referenceRange,
  sizeWords,
  statusWords,
  whereItRuns,
} from "./media";

const model = (over: Partial<ImageModel>): ImageModel => ({
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
  ...over,
});

it("reads a media address, and nothing else", () => {
  expect(mediaFromPath("/media/images")).toEqual({ door: "images", id: null });
  expect(mediaFromPath("/media/images/abc_1-2")).toEqual({ door: "images", id: "abc_1-2" });
  expect(mediaFromPath("/media/sculpture")).toBeNull();
  expect(mediaFromPath("/chats/abc")).toBeNull();
});

it("offers a listed setting as a list, a null one as free text, and an empty one not at all", () => {
  expect(offer(["auto", "low", "high"])).toEqual({ kind: "list", values: ["low", "high"] });
  expect(offer(null)).toEqual({ kind: "free" });
  expect(offer([])).toEqual({ kind: "none" });
  expect(offer(["auto"])).toEqual({ kind: "none" });
});

it("bounds how many and how many references by the listing", () => {
  expect(maxImages(model({ maxImages: 1 }))).toBe(1);
  expect(maxImages(model({ maxImages: 10 }))).toBe(10);
  expect(maxImages(model({ maxImages: null }))).toBe(10);
  expect(referenceRange(model({}))).toEqual([0, 4]);
  expect(referenceRange(model({ minReferences: 1, maxReferences: null }))).toEqual([1, 16]);
  expect(referenceRange(model({ maxReferences: 0, edits: false }))).toEqual([0, 0]);
});

it("says where a model runs, naming the account rather than claiming a charge", () => {
  expect(whereItRuns(model({}))).toBe(
    "Runs on OpenRouter (openrouter), outside your machines. Any charge goes to that account.",
  );
  expect(whereItRuns(model({ locality: "local" }))).toBe("Runs on your own machines.");
  expect(whereItRuns(model({ locality: "unknown" }))).toMatch(/cannot tell/);
});

it("groups models by account, your own machines first, and finds by name", () => {
  const groups = groupModels([
    model({ id: "openrouter/b" }),
    model({ id: "oai/a", provider: "OpenAI", account: "oai" }),
    model({ id: "local-sd", locality: "local", provider: null, account: null }),
  ]);
  expect(groups.map((g) => g.label)).toEqual(["On your machines", "OpenAI", "OpenRouter"]);
  expect(
    groupModels([model({ id: "x/flux" }), model({ id: "x/mini" })], "MIN")[0]!.models,
  ).toHaveLength(1);
});

it("says what was asked beside what came back when they differ", () => {
  const file = {
    id: "f",
    name: "i.png",
    mediaType: "image/png",
    size: 1,
    width: 1024,
    height: 1024,
  };
  expect(sizeWords("333x333", file)).toBe("Asked 333 × 333, got 1024 × 1024");
  expect(sizeWords("1024x1024", file)).toBe("1024 × 1024");
  expect(sizeWords(undefined, file)).toBe("1024 × 1024");
});

it("says a stopped or interrupted result may still be billed", () => {
  const item = { status: "stopped", error: null } as unknown as MediaItem;
  expect(statusWords(item)).toMatch(/may still bill/);
  expect(statusWords({ ...item, status: "interrupted" })).toMatch(/may have billed/);
  expect(
    statusWords({
      ...item,
      status: "failed",
      error: { message: "n: at most 1", param: "n", status: 400 },
    }),
  ).toBe("n: at most 1");
  expect(statusWords({ ...item, status: "done" })).toBeNull();
});

it("sends only what was chosen", () => {
  expect(
    imageBody({
      model: "m",
      prompt: "  a barn ",
      size: null,
      n: 1,
      quality: "",
      background: " ",
      outputFormat: "png",
      references: [],
    }),
  ).toEqual({ model: "m", prompt: "a barn", outputFormat: "png" });
});

it("finds the form field a gateway refusal names", () => {
  expect(fieldOf("output_format")).toBe("outputFormat");
  expect(fieldOf("images[0].image_url")).toBe("references");
  expect(fieldOf("n")).toBe("n");
  expect(fieldOf(null)).toBeNull();
});
