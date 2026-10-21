export const MAX_DIFF_LINES = 5_000;
export type DiffLine = { type: "same" | "added" | "removed"; text: string };

const lines = (text: string) => (text === "" ? [] : text.split(/\r?\n/));

/** Myers' bidirectional search: find a middle crossing using linear memory. */
function crossing(
  a: string[],
  b: string[],
  loA: number,
  hiA: number,
  loB: number,
  hiB: number,
): [number, number] | null {
  const n = hiA - loA,
    m = hiB - loB;
  const limit = Math.ceil((n + m) / 2);
  const offset = limit + 1;
  const forward = new Int32Array(2 * limit + 3).fill(-1);
  const backward = new Int32Array(2 * limit + 3).fill(-1);
  forward[offset + 1] = backward[offset + 1] = 0;
  const delta = n - m;
  const odd = delta % 2 !== 0;
  for (let depth = 0; depth <= limit; depth++) {
    for (let k = -depth; k <= depth; k += 2) {
      const at = offset + k;
      let x =
        k === -depth || (k !== depth && forward[at - 1]! < forward[at + 1]!)
          ? forward[at + 1]!
          : forward[at - 1]! + 1;
      let y = x - k;
      while (x < n && y < m && a[loA + x] === b[loB + y]) {
        x++;
        y++;
      }
      forward[at] = x;
      const reverseAt = offset + delta - k;
      if (
        odd &&
        reverseAt >= 0 &&
        reverseAt < backward.length &&
        backward[reverseAt]! >= 0 &&
        x >= n - backward[reverseAt]!
      )
        return [loA + x, loB + y];
    }
    for (let k = -depth; k <= depth; k += 2) {
      const at = offset + k;
      let x =
        k === -depth || (k !== depth && backward[at - 1]! < backward[at + 1]!)
          ? backward[at + 1]!
          : backward[at - 1]! + 1;
      let y = x - k;
      while (x < n && y < m && a[hiA - x - 1] === b[hiB - y - 1]) {
        x++;
        y++;
      }
      backward[at] = x;
      const forwardAt = offset + delta - k;
      if (
        !odd &&
        forwardAt >= 0 &&
        forwardAt < forward.length &&
        forward[forwardAt]! >= 0 &&
        forward[forwardAt]! >= n - x
      ) {
        const split = forward[forwardAt]!;
        return [loA + split, loB + split - (delta - k)];
      }
    }
  }
  return null;
}

/** A shortest line edit script. A final newline is a line, so it cannot disappear. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = lines(before),
    b = lines(after);
  if (a.length > MAX_DIFF_LINES || b.length > MAX_DIFF_LINES) {
    throw new Error("Too long to compare here. Download both.");
  }
  const result: DiffLine[] = [];
  function compare(loA: number, hiA: number, loB: number, hiB: number) {
    while (loA < hiA && loB < hiB && a[loA] === b[loB]) {
      result.push({ type: "same", text: a[loA++]! });
      loB++;
    }
    let suffix = 0;
    while (loA < hiA - suffix && loB < hiB - suffix && a[hiA - suffix - 1] === b[hiB - suffix - 1])
      suffix++;
    const endA = hiA - suffix,
      endB = hiB - suffix;
    const split = loA < endA && loB < endB ? crossing(a, b, loA, endA, loB, endB) : null;
    if (
      split &&
      !(split[0] === loA && split[1] === loB) &&
      !(split[0] === endA && split[1] === endB)
    ) {
      compare(loA, split[0], loB, split[1]);
      compare(split[0], endA, split[1], endB);
    } else {
      for (let i = loA; i < endA; i++) result.push({ type: "removed", text: a[i]! });
      for (let i = loB; i < endB; i++) result.push({ type: "added", text: b[i]! });
    }
    for (let i = endA; i < hiA; i++) result.push({ type: "same", text: a[i]! });
  }
  compare(0, a.length, 0, b.length);
  return result;
}
