/**
 * Eugene at the bench (workbench.md §6.1): one animated SVG per scene in
 * `public/scenes/`, each with its workshop phrase. The scene is decoration
 * beside the plain progress line, never instead of it. A scene is its file
 * plus a line here, and the gate (`scenes.test.ts`) fails on either alone.
 */

export interface Scene {
  file: string;
  phrase: string;
}

export const SCENES: readonly Scene[] = [
  { file: "eugene-measuring.svg", phrase: "Measuring twice…" },
];

/** A wait shorter than this shows only the plain line, so a fast answer never flashes Eugene. */
export const SCENE_DELAY_MS = 600;

/** A long wait moves to the next scene after about three loops. */
export const SCENE_TURN_MS = 12_000;
