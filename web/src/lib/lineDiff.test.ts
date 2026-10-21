import { lineDiff, MAX_DIFF_LINES } from "./lineDiff";

it("marks_added_and_removed_lines", () => {
  expect(lineDiff("same\nold\nend", "same\nnew\nend")).toEqual([
    { type: "same", text: "same" },
    { type: "removed", text: "old" },
    { type: "added", text: "new" },
    { type: "same", text: "end" },
  ]);
});

it("keeps blank lines, Windows line endings and a changed final newline", () => {
  expect(lineDiff("a\r\n\r\nb\r\n", "a\n\nb\n")).toEqual([
    { type: "same", text: "a" },
    { type: "same", text: "" },
    { type: "same", text: "b" },
    { type: "same", text: "" },
  ]);
  expect(lineDiff("a", "a\n")).toEqual([
    { type: "same", text: "a" },
    { type: "added", text: "" },
  ]);
  expect(lineDiff("", "x")).toEqual([{ type: "added", text: "x" }]);
  expect(lineDiff("x", "")).toEqual([{ type: "removed", text: "x" }]);
});

it("refuses either revision over 5000 lines", () => {
  const fits = Array.from({ length: MAX_DIFF_LINES }, () => "x").join("\n");
  expect(lineDiff(fits, fits)).toHaveLength(MAX_DIFF_LINES);
  expect(() => lineDiff(fits + "\nx", "x")).toThrow("Too long to compare here. Download both.");
  expect(() => lineDiff("x", fits + "\nx")).toThrow("Too long to compare here. Download both.");
});

it("finds a shortest edit path, including repeated and wholly different lines", () => {
  // An independent LCS oracle on many small inputs checks minimality, as well as reconstruction.
  const samples = ["", "a", "b", "a\na", "a\nb", "b\na", "a\nb\na", "c\nc\nc"];
  let seed = 31;
  const random = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed;
  };
  for (let i = 0; i < 24; i++)
    samples.push(Array.from({ length: random() % 18 }, () => String(random() % 4)).join("\n"));
  for (const before of samples)
    for (const after of samples) {
      const a = before ? before.split("\n") : [],
        b = after ? after.split("\n") : [];
      const table = Array.from({ length: a.length + 1 }, () => Array<number>(b.length + 1).fill(0));
      for (let i = 1; i <= a.length; i++)
        for (let j = 1; j <= b.length; j++) {
          table[i]![j] =
            a[i - 1] === b[j - 1]
              ? table[i - 1]![j - 1]! + 1
              : Math.max(table[i - 1]![j]!, table[i]![j - 1]!);
        }
      const result = lineDiff(before, after);
      expect(result.filter((line) => line.type !== "added").map((line) => line.text)).toEqual(a);
      expect(result.filter((line) => line.type !== "removed").map((line) => line.text)).toEqual(b);
      expect(result.filter((line) => line.type !== "same")).toHaveLength(
        a.length + b.length - 2 * table[a.length]![b.length]!,
      );
    }
});

it("compares wholly different revisions at the line limit", () => {
  const result = lineDiff(
    Array(MAX_DIFF_LINES).fill("old").join("\n"),
    Array(MAX_DIFF_LINES).fill("new").join("\n"),
  );
  expect(result.filter((line) => line.type === "removed")).toHaveLength(MAX_DIFF_LINES);
  expect(result.filter((line) => line.type === "added")).toHaveLength(MAX_DIFF_LINES);
});
