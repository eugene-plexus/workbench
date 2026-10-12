import { useEffect, useState } from "react";

/** A value that follows `value` after it has stopped changing for `ms`: a word typed in a box is
 * asked about once it is typed, not at every key. */
export function useDebounced<T>(value: T, ms: number): T {
  const [later, setLater] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setLater(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return later;
}
