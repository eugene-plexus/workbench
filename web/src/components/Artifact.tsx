/**
 * Artifacts in an answer (workbench-rich-content-roadmap.md RC5, RC6): a block
 * the answer asked to be one (an `html artifact title="…"` fence) is drawn where
 * it stands (ArtifactBody.tsx): Markdown as it reads, SVG as a picture, a page
 * or a component in its frame, run by itself once on screen when the person
 * lets previews run (`RunPreviews`), never in the owner's view of someone
 * else's chat. Open shows it beside the chat (ArtifactDialog.tsx) with its
 * source, revisions and download. Any other closed block of Markdown, SVG,
 * HTML, JSX or TSX has Open as artifact in its head.
 *
 * Only a message's answer is a scope for them (`ArtifactScope`, from
 * MessageView.tsx): the same Markdown elsewhere (a draft, a preview of a
 * Markdown artifact) has plain blocks.
 */

import { AppWindow, FileCode, FileText, Image as ImageIcon, PanelRightOpen } from "lucide-react";
import { createContext, lazy, Suspense, useContext, useMemo, useState } from "react";
import {
  type ArtifactKind,
  type ArtifactSummary,
  blockText,
  KIND_NAMES,
  keptFor,
  revisionOrigin,
} from "../lib/artifacts";

/** What a block needs to know to be an artifact: whose message it is in, and what is kept. */
export type Scope = {
  chatId: string;
  messageId: string;
  artifacts: ArtifactSummary[];
  /** The owner reading someone's chat: nothing is kept from here. */
  readOnly: boolean;
  /** The answer is still being written: nothing is kept until it ends. */
  writing: boolean;
  /** The chat lets pages and components run by themselves; unset is on. */
  runArtifacts?: boolean;
  /** A tool result's server/tool identity, when this scope is a tool's. */
  tool?: string;
  /** Something was kept: the chat is read again, for the cards' counts. */
  onKept: () => void;
};

export const ArtifactScope = createContext<Scope | null>(null);

/** Whether the person lets previews run when shown (theirs, kept by Workbench; App.tsx). */
export const RunPreviews = createContext(true);

// Loaded when an artifact is first opened, or drawn: a chat of words alone fetches neither.
const ArtifactDialog = lazy(() => import("./ArtifactDialog"));
const ArtifactBody = lazy(() => import("./ArtifactBody"));

export type Opened = {
  kind: ArtifactKind;
  language: string | null;
  title: string;
  /** The block's words, as the answer has them (revision 1). */
  original: string;
};

function useOpened(): [Opened | null, (opened: Opened | null) => void] {
  return useState<Opened | null>(null);
}

function Dialog({ scope, opened, onClose }: { scope: Scope; opened: Opened; onClose: () => void }) {
  return (
    <Suspense fallback={null}>
      <ArtifactDialog scope={scope} opened={opened} onClose={onClose} />
    </Suspense>
  );
}

const ICONS = { markdown: FileText, svg: ImageIcon, html: AppWindow, react: FileCode } as const;

function revisionWords(kept: ArtifactSummary | null, tool?: string): string {
  if (!kept || kept.revisions <= 1) {
    return tool ? `Revision 1, ${revisionOrigin(tool)}` : "From the answer";
  }
  return `${kept.revisions} revisions`;
}

/** A block the answer asked to be an artifact: drawn where it stands, with its title, kind,
 * revisions, and Open. */
export function ArtifactCard({
  kind,
  language,
  title,
  source,
  openAs = false,
}: {
  kind: ArtifactKind;
  language: string | null;
  title: string;
  source: string;
  /** A tool's text block also offers Open as artifact. */
  openAs?: boolean;
}) {
  const scope = useContext(ArtifactScope);
  const runPreviews = useContext(RunPreviews);
  const [opened, setOpened] = useOpened();
  const [original, kept] = useMemo(() => {
    // A tool's text is already the original; only a parsed fence adds a newline.
    const original = openAs ? source : blockText(source);
    const kept = keptFor(scope?.artifacts, kind, original);
    if (!kept && openAs && source.endsWith("\n")) {
      // Earlier tool cards stripped that newline before saving revision one.
      const earlier = blockText(source);
      const saved = keptFor(scope?.artifacts, kind, earlier);
      if (saved) return [earlier, saved] as const;
    }
    return [original, kept] as const;
  }, [scope?.artifacts, kind, source, openAs]);
  const Icon = ICONS[kind];
  const shownTitle = kept?.title ?? title;
  return (
    <div className="artifact-card" data-testid="artifact-card" data-kind={kind}>
      <div className="artifact-card-head">
        <Icon size={18} aria-hidden="true" className="shrink-0" />
        <span className="artifact-card-words">
          <span className="artifact-card-title">{shownTitle}</span>
          <span className="text-sm text-muted">
            {KIND_NAMES[kind]} · {revisionWords(kept, kept?.tool ?? scope?.tool)}
          </span>
        </span>
        <button
          type="button"
          className="act"
          aria-haspopup="dialog"
          aria-label={
            openAs ? `Open this ${KIND_NAMES[kind]} as an artifact` : `Open ${shownTitle}`
          }
          disabled={!scope}
          onClick={() => setOpened({ kind, language, title: shownTitle, original })}
        >
          <PanelRightOpen size={14} aria-hidden="true" />
          {openAs ? "Open as artifact" : "Open"}
        </button>
      </div>
      {scope && (
        <Suspense fallback={null}>
          <ArtifactBody
            key={scope.runArtifacts === false ? "manual" : "automatic"}
            scope={scope}
            kind={kind}
            title={shownTitle}
            original={original}
            kept={kept}
            auto={runPreviews && scope.runArtifacts !== false && !scope.readOnly && !scope.writing}
          />
        </Suspense>
      )}
      {scope && opened && <Dialog scope={scope} opened={opened} onClose={() => setOpened(null)} />}
    </div>
  );
}

/** Open as artifact, in a plain block's head; nothing outside an answer. */
export function OpenAsArtifact({
  kind,
  language,
  source,
}: {
  kind: ArtifactKind;
  language: string | null;
  source: string;
}) {
  const scope = useContext(ArtifactScope);
  const [opened, setOpened] = useOpened();
  const original = blockText(source);
  const kept = useMemo(
    () => keptFor(scope?.artifacts, kind, original),
    [scope?.artifacts, kind, original],
  );
  if (!scope) return null;
  const title = kept?.title ?? KIND_NAMES[kind];
  return (
    <>
      <button
        type="button"
        className="act"
        aria-haspopup="dialog"
        aria-label={
          kept ? `Open the artifact ${title}` : `Open this ${KIND_NAMES[kind]} as an artifact`
        }
        onClick={() => setOpened({ kind, language, title, original })}
      >
        <PanelRightOpen size={14} aria-hidden="true" />
        <span className="diagram-tool-label">
          {kept && kept.revisions > 1 ? `Artifact · ${kept.revisions}` : "Open as artifact"}
        </span>
      </button>
      {opened && <Dialog scope={scope} opened={opened} onClose={() => setOpened(null)} />}
    </>
  );
}
