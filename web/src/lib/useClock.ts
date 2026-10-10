import { useEffect, useState } from "react";

/** The time now, in seconds, ticking each second while `running`: a running
 * video says how long it has run (§5), and so does an answer still thinking. */
export function useClock(running: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!running) return;
    const tick = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(tick);
  }, [running]);
  return running ? now : Date.now() / 1000;
}
