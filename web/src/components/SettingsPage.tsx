/**
 * The Settings page (MV4: app before config): every setting a person has in Workbench, apart from
 * the pages that hold their data. Memory's, then Documents', then the connections other apps have.
 * Each section links to its page, and each page links here (to its section, by the address's
 * `#memory` or `#documents`).
 */

import { ArrowLeft, Settings as SettingsIcon } from "lucide-react";
import { useEffect } from "react";

import { Connections } from "./Connections";
import { DocumentsSettings } from "./DocumentsSettings";
import { MemorySettings } from "./MemorySettings";

export function SettingsPage({
  onClose,
  onOpenMemory,
  onOpenDocuments,
}: {
  onClose: () => void;
  onOpenMemory: () => void;
  onOpenDocuments: () => void;
}) {
  // A page's own link names its section: it is brought into view once the page is drawn.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView?.({ block: "start" });
  }, []);

  return (
    <section aria-label="Settings" className="page" data-testid="settings-page">
      <div className="page-col">
        <header className="page-head">
          <h1 id="view-heading" tabIndex={-1} className="title-page">
            <SettingsIcon size={20} aria-hidden />
            Settings
          </h1>
          <button type="button" data-testid="settings-back" className="btn" onClick={onClose}>
            <ArrowLeft size={14} aria-hidden />
            Back to chat
          </button>
        </header>
        <p className="help">
          Your own settings in this Workbench. Each says what is in effect and whose choice it is.
        </p>
        <MemorySettings onOpenMemory={onOpenMemory} />
        <DocumentsSettings onOpenDocuments={onOpenDocuments} />
        <div id="connections" className="scroll-mt-4">
          <Connections />
        </div>
      </div>
    </section>
  );
}
