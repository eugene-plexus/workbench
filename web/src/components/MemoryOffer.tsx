/**
 * The offer of Learn from my chats (MR8), on an empty chat after a person's fifth: once, until
 * they answer it. Turning it on here is the same choice as on the Memory page; "No thanks" is
 * remembered and the offer is not made again.
 */

import { Lightbulb } from "lucide-react";
import { useEffect, useState } from "react";

import { api, put } from "../lib/api";
import { failureWords } from "../lib/memory";

export function MemoryOffer({ onOpenMemory }: { onOpenMemory: () => void }) {
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // The offer is a nicety: a page that cannot ask is a page without it.
    void (async () => {
      try {
        const offer = await api<{ show: boolean } | undefined>("/api/memory/offer");
        if (live) setShow(Boolean(offer?.show));
      } catch {
        if (live) setShow(false);
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  async function answer(body: Record<string, unknown>, words: string) {
    setBusy(true);
    setProblem(null);
    try {
      await put("/api/memory/prefs", { offerDismissed: true, ...body });
      setDone(words);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <p
        role="status"
        data-testid="memory-offer-done"
        className="notice notice--ok mx-auto max-w-xl"
      >
        {done}
      </p>
    );
  }
  if (!show) return null;
  return (
    <div data-testid="memory-offer" className="notice mx-auto max-w-xl">
      <Lightbulb size={16} aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <span>
          <span className="notice-title">Let Workbench suggest notes?</span>
          When a chat goes quiet, its model can suggest up to three notes from what you wrote, such
          as your job or how you like answers. You keep the ones that are right on the Memory page;
          nothing is kept until you do.
        </span>
        {problem && <span role="alert">{problem}</span>}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="memory-offer-yes"
            disabled={busy}
            className="btn btn-primary"
            onClick={() =>
              void answer(
                { learn: "suggest" },
                "Suggestions are on. Turn them off on the Memory page.",
              )
            }
          >
            Suggest notes
          </button>
          <button
            type="button"
            data-testid="memory-offer-no"
            disabled={busy}
            className="btn"
            onClick={() => void answer({ learn: "off" }, "Suggestions stay off.")}
          >
            No thanks
          </button>
          <button
            type="button"
            data-testid="memory-offer-more"
            disabled={busy}
            className="btn btn-quiet"
            onClick={onOpenMemory}
          >
            Open Memory
          </button>
        </div>
      </div>
    </div>
  );
}
