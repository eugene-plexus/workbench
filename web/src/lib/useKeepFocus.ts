/**
 * A control that is disabled while its work runs loses the keyboard's place:
 * the browser moves focus to the page, and the next key goes nowhere. Call the
 * function this returns where the work starts (it notes where focus is), and
 * once `busy` is over, focus goes back there, unless the person has already
 * gone somewhere else or what had it is gone.
 */

import { useCallback, useEffect, useRef } from "react";

export function useKeepFocus(busy: boolean): () => void {
  const was = useRef<Element | null>(null);
  useEffect(() => {
    if (busy || !was.current) return;
    const target = was.current;
    was.current = null;
    const now = document.activeElement;
    if (target instanceof HTMLElement && target.isConnected && (!now || now === document.body)) {
      target.focus();
    }
  }, [busy]);
  return useCallback(() => {
    was.current = document.activeElement;
  }, []);
}
