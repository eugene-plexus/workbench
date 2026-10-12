import { useEffect, useState } from "react";

import { serverNow } from "./api";

/** The time now by Workbench's clock, in seconds, read again each `everyMs` so that "checked 3
 * minutes ago" and "next check in 7 minutes" stay true between the reads that fetch the folders.
 * Each answer from Workbench also corrects the clock, so the time is read afresh whenever the page
 * redraws for one of them (`again`). */
export function useServerNow(everyMs = 10_000, again?: unknown): number {
  const [now, setNow] = useState(serverNow);
  useEffect(() => {
    const tick = window.setInterval(() => setNow(serverNow()), everyMs);
    return () => window.clearInterval(tick);
  }, [everyMs]);
  useEffect(() => {
    setNow(serverNow());
  }, [again]);
  return now;
}
