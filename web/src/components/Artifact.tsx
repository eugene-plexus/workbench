/**
 * Artifacts in an answer (workbench-rich-content-roadmap.md RC5): a block the
 * answer asked to be one (an `html artifact title="…"` fence) is a card that
 * opens it beside the chat, and any other closed block of Markdown, SVG,
 * HTML, JSX or TSX has Open as artifact in its head. Opening one runs
 * nothing: the dialog (ArtifactDialog.tsx) shows Markdown and SVG as they
 * read, and a page or a component only when the person presses Run.
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
  /** Something was kept: the chat is read again, for the cards' counts. */
  onKept: () => void;
};

export const ArtifactScope = createContext<Scope | null>(null);

// Loaded when an artifact is first opened: a chat of words alone never fetches it.
const ArtifactDialog = lazy(() => import("./ArtifactDialog"));

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

function revisionWords(kept: ArtifactSummary | null): string {
  if (!kept || kept.revisions <= 1) return "From the answer";
  return `${kept.revisions} revisions`;
}

/** A block the answer asked to be an artifact: its title, kind and revisions, and Open. */
export function ArtifactCard({
  kind,
  language,
  title,
  source,
}: {
  kind: ArtifactKind;
  language: string | null;
  title: string;
  source: string;
}) {
  const scope = useContext(ArtifactScope);
  const [opened, setOpened] = useOpened();
  const original = blockText(source);
  const kept = useMemo(
    () => keptFor(scope?.artifacts, kind, original),
    [scope?.artifacts, kind, original],
  );
  const Icon = ICONS[kind];
  const shownTitle = kept?.title ?? title;
  return (
    <div className="artifact-card" data-testid="artifact-card" data-kind={kind}>
      <Icon size={18} aria-hidden="true" className="shrink-0" />
      <span className="artifact-card-words">
        <span className="artifact-card-title">{shownTitle}</span>
        <span className="text-sm text-muted">
          {KIND_NAMES[kind]} · {revisionWords(kept)}
        </span>
      </span>
      <button
        type="button"
        className="act"
        aria-haspopup="dialog"
        aria-label={`Open ${shownTitle}`}
        disabled={!scope}
        onClick={() => setOpened({ kind, language, title: shownTitle, original })}
      >
        <PanelRightOpen size={14} aria-hidden="true" />
        Open
      </button>
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
