/**
 * Eugene, from the website, byte for byte (workbench.md §6). Decoration,
 * never information: no alternative text, never on the chat itself, and
 * always beside the sentence that says what is going on.
 */

export type Pose = "welcome" | "guide" | "working" | "curious";

export function Mascot({ pose, size = 160 }: { pose: Pose; size?: number }) {
  return (
    <img
      src={`/mascots/eugene-${pose}.svg`}
      alt=""
      aria-hidden="true"
      width={size}
      height={size}
      className="select-none"
      draggable={false}
    />
  );
}

export function Logo({ size = 28 }: { size?: number }) {
  return <img src="/eugene-face.svg" alt="" aria-hidden="true" width={size} height={size} />;
}
