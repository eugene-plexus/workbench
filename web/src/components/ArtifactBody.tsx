/**
 * An artifact drawn where its answer puts it (workbench-rich-content-roadmap.md
 * RC6): Markdown as an answer reads, SVG as a picture by RC2's rules, a page or
 * a component in its frame, run when it is on screen if the person lets
 * previews run by themselves (ArtifactPreview.tsx). What is shown is the last
 * revision kept of it, else the answer's block.
 *
 * Loaded with the first artifact on the page: a chat of words alone never
 * fetches it.
 */

import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import {
  type ArtifactKind,
  type ArtifactSummary,
  type ArtifactView,
  type RevisionView,
  digest,
  runs,
} from "../lib/artifacts";
import { readSvg, Refused } from "../lib/svgArtifact";
import { ArtifactScope, type Scope } from "./Artifact";
import { ArtifactPreview } from "./ArtifactPreview";
import { Block } from "./Block";
import { Markdown } from "./Markdown";

export function SvgPicture({ source, title }: { source: string; title: string }) {
  const read = useMemo(() => {
    try {
      return { picture: readSvg(source, "#ffffff") };
    } catch (error) {
      return { reason: error instanceof Refused ? error.message : String(error) };
    }
  }, [source]);
  if ("reason" in read) {
    return (
      <Block
        language="svg"
        source={source}
        open={false}
        note={`Picture not shown: ${read.reason}`}
      />
    );
  }
  return (
    <div className="artifact-picture" tabIndex={0} role="group" aria-label={title}>
      <img
        src={read.picture.src}
        width={read.picture.width}
        height={read.picture.height}
        alt={read.picture.title || title}
      />
    </div>
  );
}

/** The last revision kept of a block, else the block: null while it is read. */
function useLatest(scope: Scope, kept: ArtifactSummary | null, original: string) {
  const later = kept !== null && kept.revisions > 1;
  const [read, setRead] = useState<{ source: string; problem: string } | null>(null);
  const keptId = kept?.id;
  const count = kept?.revisions;
  useEffect(() => {
    if (!later || !keptId) return;
    let gone = false;
    const path = `/api/chats/${scope.chatId}/artifacts/${keptId}`;
    api<ArtifactView>(path)
      .then((view) => {
        const last = view.revisionList[view.revisionList.length - 1]?.number ?? 1;
        return last === 1
          ? original
          : api<RevisionView>(`${path}/revisions/${last}`).then((r) => r.source ?? original);
      })
      .then(
        (source) => {
          if (!gone) setRead({ source, problem: "" });
        },
        (error: unknown) => {
          if (gone) return;
          setRead({
            source: original,
            problem:
              "Its saved revisions could not be read, so this is the answer's block: " +
              (error instanceof Error ? error.message : String(error)),
          });
        },
      );
    return () => {
      gone = true;
    };
  }, [later, keptId, count, scope.chatId, original]);
  if (!later) return { source: original, problem: "" };
  return read;
}

export default function ArtifactBody({
  scope,
  kind,
  title,
  original,
  kept,
  auto,
}: {
  scope: Scope;
  kind: ArtifactKind;
  title: string;
  original: string;
  kept: ArtifactSummary | null;
  /** A page or a component runs by itself when on screen. */
  auto: boolean;
}) {
  const latest = useLatest(scope, kept, original);
  const blockKey = useMemo(
    () => `${scope.messageId}:${kind}:${digest(original)}`,
    [scope.messageId, kind, original],
  );
  const shownKey = useMemo(() => (latest ? digest(latest.source) : ""), [latest]);
  if (!latest) return <p className="text-sm text-muted">Reading its last revision…</p>;
  let body;
  if (kind === "markdown") {
    body = (
      <div className="artifact-doc" tabIndex={0} role="region" aria-label={title}>
        {/* Its own blocks are plain: an artifact holds no artifacts of its own. */}
        <ArtifactScope.Provider value={null}>
          <Markdown text={latest.source} />
        </ArtifactScope.Provider>
      </div>
    );
  } else if (kind === "svg") {
    body = <SvgPicture source={latest.source} title={title} />;
  } else if (runs(kind)) {
    body = (
      <ArtifactPreview
        key={shownKey}
        kind={kind === "html" ? "html" : "react"}
        source={latest.source}
        title={title}
        blockKey={blockKey}
        inline
        auto={auto}
      />
    );
  }
  return (
    <div className="artifact-body" data-testid="artifact-body">
      {latest.problem && (
        <p role="alert" className="text-sm text-error">
          {latest.problem}
        </p>
      )}
      {body}
    </div>
  );
}
