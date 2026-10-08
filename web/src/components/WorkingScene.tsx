import { type ReactNode, useEffect, useState } from "react";

import { SCENE_DELAY_MS, SCENE_TURN_MS, SCENES } from "../lib/scenes";
import { Mascot } from "./Mascot";

const REDUCED = "(prefers-reduced-motion: reduce)";

const reducedNow = () =>
  typeof window.matchMedia === "function" && window.matchMedia(REDUCED).matches;

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(reducedNow);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(REDUCED);
    const changed = () => setReduced(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  return reduced;
}

/**
 * Eugene at the bench while a model works (workbench.md §6.1), beside the
 * plain progress line it is given as children. The scene appears only once
 * an active wait outlasts SCENE_DELAY_MS, moves to the next scene every
 * SCENE_TURN_MS, and is hidden from screen readers: the plain line is the
 * state, and it stays mounted whether the scene shows or not. With reduced
 * motion asked for, the still working pose stands in for the loop and the
 * phrase stays.
 */
export function WorkingScene({
  active,
  size = 72,
  children,
}: {
  active: boolean;
  size?: number;
  children: ReactNode;
}) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(false);
  const [index, setIndex] = useState(() => Math.floor(Math.random() * SCENES.length));

  useEffect(() => {
    if (!active) return;
    const show = window.setTimeout(() => setShown(true), SCENE_DELAY_MS);
    const turn = window.setInterval(() => setIndex((i) => (i + 1) % SCENES.length), SCENE_TURN_MS);
    return () => {
      window.clearTimeout(show);
      window.clearInterval(turn);
      setShown(false);
    };
  }, [active]);

  const scene = active && shown ? SCENES[index] : undefined;
  return (
    <div className="flex items-center gap-3">
      {scene && (
        <div aria-hidden="true" className="scene-in shrink-0" data-testid="working-scene">
          {reduced ? (
            <Mascot pose="working" size={size} />
          ) : (
            <img
              src={`/scenes/${scene.file}`}
              alt=""
              width={size}
              height={size}
              className="select-none"
              draggable={false}
              data-testid="scene-art"
            />
          )}
        </div>
      )}
      <div className="flex min-w-0 flex-col">
        {scene && (
          <span
            aria-hidden="true"
            className="scene-in italic text-muted"
            data-testid="scene-phrase"
          >
            {scene.phrase}
          </span>
        )}
        {children}
      </div>
    </div>
  );
}
