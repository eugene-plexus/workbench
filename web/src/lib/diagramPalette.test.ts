import { accessiblePalette } from "./diagramClient";

const palette = {
  cScale1: "#38865a",
  cScale2: "#976a25",
  git1: "#38865a",
  primaryColor: "#2b353d",
};
const ratio = (a: string, b: string) => {
  const luminance = (hex: string) => {
    const rgb = [1, 3, 5]
      .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return rgb[0]! * 0.2126 + rgb[1]! * 0.7152 + rgb[2]! * 0.0722;
  };
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0]! + 0.05) / (values[1]! + 0.05);
};

it.each([
  "Mozilla/5.0 Firefox/155.0",
  "Mozilla/5.0 AppleWebKit/605.1.15 Version/26.6 Safari/605.1.15",
])("keeps section labels above 4.5:1 in %s", (ua) => {
  const result = accessiblePalette(palette, ua);
  for (const key of ["cScale1", "cScale2", "git1"])
    expect(ratio("#f4f6f7", result[key]!)).toBeGreaterThanOrEqual(4.5);
  expect(result.primaryColor).toBe(palette.primaryColor);
});

it("preserves Chromium's palette and leaves the input untouched", () => {
  expect(
    accessiblePalette(palette, "Mozilla/5.0 AppleWebKit/537.36 Chrome/150.0 Safari/537.36"),
  ).toEqual(palette);
  expect(palette.cScale1).toBe("#38865a");
});
