import {
  dollars,
  elapsedWords,
  fieldOf,
  type MediaItem,
  quoteWords,
  statusWords,
  type VideoModel,
  type VideoPrice,
  videoQuote,
  videoWords,
} from "./media";

const second = (usd: number, over: Partial<VideoPrice> = {}): VideoPrice => ({
  sku: "s",
  per: "second",
  usd,
  ...over,
});

const model = (prices: VideoPrice[] | null, over: Partial<VideoModel> = {}): VideoModel => ({
  id: "x-ai/grok-imagine-video",
  account: "openrouter",
  provider: "OpenRouter",
  locality: "external",
  ready: true,
  onDemand: false,
  durations: [1, 15],
  sizes: ["854x480", "1280x720"],
  firstFrame: true,
  prices,
  ...over,
});

/** grok-imagine-video's list, as OpenRouter gave it on 2026-10-08. */
const grok = model([
  { sku: "cents_per_image_input", per: "input_image", usd: 0.002 },
  second(0.05, { resolution: "480p", sizes: ["854x480"] }),
  second(0.07, { resolution: "720p", sizes: ["1280x720"] }),
]);

it("prices a video from the line for the size sent", () => {
  expect(videoQuote(grok, { seconds: 12, size: "854x480", firstFrame: false })).toEqual({
    low: expect.closeTo(0.6),
    high: expect.closeTo(0.6),
    resolution: "480p",
  });
  // A first frame is an input image, priced on top.
  // Six places: two would let 5¢ pass for 5.2¢.
  expect(videoQuote(grok, { seconds: 1, size: "854x480", firstFrame: true })?.high).toBeCloseTo(
    0.052,
    6,
  );
  // No size sent leaves the resolution to the provider: a range.
  expect(videoQuote(grok, { seconds: 10, size: null, firstFrame: false })).toEqual({
    low: expect.closeTo(0.5),
    high: expect.closeTo(0.7),
    resolution: null,
  });
});

it("gives a range where sound is the provider's default, and holds a minimum", () => {
  const veo = model([
    second(0.4, { audio: true }),
    second(0.2, { audio: false }),
    second(0.6, { audio: true, resolution: "4K", sizes: ["3840x2160"] }),
  ]);
  expect(videoQuote(veo, { seconds: 4, size: "1280x720", firstFrame: false })).toEqual({
    low: expect.closeTo(0.8),
    high: expect.closeTo(1.6),
    resolution: null,
  });
  expect(videoQuote(veo, { seconds: 4, size: "3840x2160", firstFrame: false })?.resolution).toBe(
    "4K",
  );
  const runway = model([second(0.28), { sku: "m", per: "minimum", usd: 0.56 }]);
  expect(videoQuote(runway, { seconds: 1, size: null, firstFrame: false })?.high).toBeCloseTo(
    0.56,
    6,
  );
});

it("prices text to video and image to video apart", () => {
  const kling = model([
    second(0.112, { firstFrame: false, resolution: "720p", sizes: ["1280x720"] }),
    second(0.15, { firstFrame: true, resolution: "720p", sizes: ["1280x720"] }),
  ]);
  const ask = { seconds: 10, size: "1280x720" };
  expect(videoQuote(kling, { ...ask, firstFrame: false })?.high).toBeCloseTo(1.12, 6);
  expect(videoQuote(kling, { ...ask, firstFrame: true })?.high).toBeCloseTo(1.5, 6);
});

it("has no price when nothing listed prices the request", () => {
  expect(videoQuote(model(null), { seconds: 4, size: null, firstFrame: false })).toBeNull();
  expect(videoQuote(grok, { seconds: null, size: "854x480", firstFrame: false })).toBeNull();
  // A size whose resolution has no line, and no line for every resolution.
  expect(videoQuote(grok, { seconds: 4, size: "1920x1080", firstFrame: false })).toBeNull();
});

it("says what sending will cost, and never that it is free", () => {
  expect(quoteWords(grok, { seconds: 12, size: "854x480", firstFrame: false })).toBe(
    "12 s at 480p. About $0.60, billed to OpenRouter.",
  );
  expect(quoteWords(grok, { seconds: 10, size: null, firstFrame: false })).toBe(
    "10 s, at the model's own size. About $0.50 to $0.70, billed to OpenRouter.",
  );
  expect(quoteWords(model(null), { seconds: 4, size: "854x480", firstFrame: false })).toBe(
    "4 s at 854 × 480. No price is listed for this; OpenRouter bills it to that account.",
  );
  expect(dollars(0.002)).toBe("under $0.01");
  expect(dollars(0)).toBe("$0.00");
});

it("says how long a job has run, and what came back beside what was asked", () => {
  expect(elapsedWords(18.7)).toBe("18 s");
  expect(elapsedWords(125)).toBe("2 min 5 s");
  expect(elapsedWords(120)).toBe("2 min");
  const item = {
    door: "video",
    status: "running",
    createdAt: 100,
    job: { status: "queued", progress: 0, polls: 2, polledAt: 0, problem: "Not reachable." },
  } as unknown as MediaItem;
  expect(statusWords(item, 118)).toBe(
    "Working, 18 s so far. You can close this tab; it keeps going. The last check did not answer: Not reachable. Workbench keeps asking.",
  );
  expect(
    videoWords({ seconds: 1, size: "854x480" }, { seconds: 1.04, width: 854, height: 480 }),
  ).toBe("1.0 s, 854 × 480");
  expect(
    videoWords({ seconds: 5, size: "854x480" }, { seconds: 1.0, width: 848, height: 480 }),
  ).toBe("Asked 5 s at 854 × 480, got 1.0 s at 848 × 480");
  expect(fieldOf("input_reference")).toBe("firstFrame");
});
