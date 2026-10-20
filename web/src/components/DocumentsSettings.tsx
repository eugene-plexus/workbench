/**
 * Documents' settings, on the Settings page (MV4: app before config): whether new chats start
 * with Documents on, and whether passages may go to models outside the person's machines. The
 * documents themselves are the Documents page's. Each control shows the value in effect and whose
 * word it is, as it did there. Where the owner has turned documents off, the section says so and
 * holds no control.
 */

import { CircleAlert, Files, Info } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { api, put } from "../lib/api";
import { failureWords } from "../lib/memory";
import {
  EXTERNAL_CHOICES,
  externalLabel,
  newChatsWords,
  OWNER_OFF,
  OWNER_OFF_EFFECT,
  setByWords,
} from "../lib/savedDocuments";
import type { DocumentsPage, ExternalChoice } from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";

type PrefsBody = { newChats?: boolean; external?: ExternalChoice | null };

export function DocumentsSettings({ onOpenDocuments }: { onOpenDocuments: () => void }) {
  const uid = useId();
  const [data, setData] = useState<DocumentsPage | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const keepFocus = useKeepFocus(busy);
  const latest = useRef(0);

  async function load() {
    const mine = ++latest.current;
    try {
      const next = await api<DocumentsPage>("/api/documents");
      if (mine !== latest.current) return;
      setData(next);
      setLoadProblem(null);
    } catch (failure) {
      if (mine !== latest.current) return;
      setLoadProblem(failureWords(failure));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function change(body: PrefsBody) {
    setProblem(null);
    keepFocus();
    setBusy(true);
    try {
      await put("/api/documents/prefs", body);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      await load();
      setBusy(false);
    }
  }

  const off = data !== null && !data.installEnabled;
  return (
    <section
      id="documents"
      aria-labelledby={`${uid}-documents`}
      data-testid="settings-documents"
      className="section scroll-mt-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={`${uid}-documents`} className="title-section flex-1">
          <Files size={18} aria-hidden />
          Documents
        </h2>
        <button
          type="button"
          data-testid="settings-open-documents"
          className="btn btn-quiet"
          onClick={onOpenDocuments}
        >
          Open Documents
        </button>
      </div>
      {loadProblem && (
        <div role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">Could not read your documents settings</span>
            {loadProblem}
            <div className="mt-2">
              <button
                type="button"
                data-testid="documents-settings-retry"
                className="btn"
                onClick={() => void load()}
              >
                Try again
              </button>
            </div>
          </div>
        </div>
      )}
      {data === null && !loadProblem && (
        <p role="status" className="text-sm text-muted">
          Loading your documents settings…
        </p>
      )}
      {off && (
        <p data-testid="documents-owner-off" className="notice">
          <Info size={16} aria-hidden />
          <span>
            <span className="notice-title">{OWNER_OFF}</span>
            {OWNER_OFF_EFFECT}
          </span>
        </p>
      )}
      {problem && (
        <p role="alert" data-testid="documents-settings-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {data && !off && (
        <DocumentsControls data={data} busy={busy} onPrefs={(body) => void change(body)} />
      )}
    </section>
  );
}

function DocumentsControls({
  data,
  busy,
  onPrefs,
}: {
  data: DocumentsPage;
  busy: boolean;
  onPrefs: (body: PrefsBody) => void;
}) {
  const uid = useId();
  const { prefs } = data;
  const { effective } = prefs;
  const ownerSet = effective.externalSetBy === "owner";
  // What was just chosen shows while the server catches up, and a refusal puts the old
  // value back: these are only read while the page is busy.
  const [wanted, setWanted] = useState<{ newChats?: boolean; external?: ExternalChoice }>({});
  const newChats = busy && wanted.newChats !== undefined ? wanted.newChats : effective.newChats;
  const external = busy && wanted.external !== undefined ? wanted.external : effective.external;
  return (
    <>
      <div className="field">
        <label className="check items-center" htmlFor={`${uid}-new`}>
          <input
            id={`${uid}-new`}
            type="checkbox"
            role="switch"
            data-testid="documents-new-chats-switch"
            aria-describedby={`${uid}-new-help`}
            checked={newChats}
            disabled={busy}
            onChange={(event) => {
              setWanted({ newChats: event.target.checked });
              onPrefs({ newChats: event.target.checked });
            }}
          />
          Documents in new chats
        </label>
        <p id={`${uid}-new-help`} data-testid="documents-new-chats-effective" className="help">
          {newChatsWords(prefs)}
        </p>
      </div>
      <fieldset className="flex min-w-0 flex-col gap-2" aria-describedby={`${uid}-outside-help`}>
        <legend className="field-label mb-1">Models that run outside your machines</legend>
        <p id={`${uid}-outside-help`} className="help">
          Some models run on another company&apos;s computers. Matching passages of your documents
          would leave your machines to reach them. This does not hold back files you attach to a
          chat.
        </p>
        {EXTERNAL_CHOICES.map((choice) => (
          <div key={choice.value}>
            <label className="check">
              <input
                type="radio"
                name={`${uid}-external`}
                value={choice.value}
                data-testid={`documents-external-${choice.value}`}
                aria-describedby={`${uid}-external-${choice.value}`}
                checked={external === choice.value}
                disabled={busy || ownerSet}
                onChange={() => {
                  setWanted({ external: choice.value });
                  onPrefs({ external: choice.value });
                }}
              />
              <span>{choice.label}</span>
            </label>
            <p id={`${uid}-external-${choice.value}`} className="help ps-[1.625rem]">
              {choice.help}
            </p>
          </div>
        ))}
        <p data-testid="documents-external-effective" className="help">
          In effect: <strong>{externalLabel(effective.external)}</strong>.{" "}
          {setByWords(effective.externalSetBy)}
        </p>
        {prefs.external !== null && !ownerSet && (
          <button
            type="button"
            data-testid="documents-external-default"
            disabled={busy}
            onClick={() => onPrefs({ external: null })}
            className="btn btn-quiet -ms-2 self-start"
          >
            Use the default
          </button>
        )}
      </fieldset>
    </>
  );
}
