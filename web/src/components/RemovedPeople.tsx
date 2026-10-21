/**
 * Removed people (row 7), on the owner's Settings page: the people Eugene no longer has, what
 * happens to their data and when, and the owner's choices for each. Their data is hidden from
 * everyone, the owner included, until it is deleted. The owner can export it (every export is
 * logged), keep it (the clock stops; nothing is given back to anyone), set it to be deleted
 * later, or delete it now: at once and for good, every copy Workbench keeps.
 *
 * When Eugene stops knowing three or more people at once, nothing is hidden until the owner
 * says they were removed (the brake). `RemovedPeopleNotice` is the owner's line above every
 * page: what is about to be deleted, or what waits for their answer.
 */

import { Info, UserX } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { api, fileOf, post } from "../lib/api";
import { failureWords, noteDate, noteTime, saveFile } from "../lib/memory";
import {
  type DataExport,
  deleteNowWords,
  noticeWords,
  type RemovedPeoplePage,
  type RemovedPerson,
  stateWords,
} from "../lib/removal";

const PATH = "/api/removed-people";

export function RemovedPeopleNotice({ onReview }: { onReview: () => void }) {
  const [words, setWords] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api<RemovedPeoplePage>(PATH)
      .then((page) => {
        if (live) setWords(noticeWords(page));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  if (!words) return null;
  return (
    <aside
      role="status"
      data-testid="removed-people-notice"
      className="notice notice--warn notice--bar"
    >
      <Info size={16} aria-hidden />
      {words}{" "}
      <button type="button" className="btn btn-quiet -my-1 ml-auto shrink-0" onClick={onReview}>
        Review
      </button>
    </aside>
  );
}

export function RemovedPeople() {
  const uid = useId();
  const [page, setPage] = useState<RemovedPeoplePage | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  async function load(path = PATH, method: "GET" | "POST" = "GET", body?: unknown) {
    setBusy(true);
    setProblem(null);
    try {
      setPage(
        method === "GET"
          ? await api<RemovedPeoplePage>(path)
          : await post<RemovedPeoplePage>(path, body),
      );
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function act(person: RemovedPerson, action: "keep" | "schedule" | "delete") {
    setBusy(true);
    setProblem(null);
    setConfirming(null);
    try {
      await post(`${PATH}/${encodeURIComponent(person.sub)}/${action}`);
      setPage(await api<RemovedPeoplePage>(PATH));
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function exportOf(person: RemovedPerson) {
    setBusy(true);
    setProblem(null);
    try {
      let made = (
        await post<{ export: DataExport }>(`/api/people/${encodeURIComponent(person.sub)}/export`)
      ).export;
      for (let i = 0; made.state === "making" && i < 600; i++) {
        await new Promise((done) => window.setTimeout(done, 1000));
        const listed = await api<{ exports: DataExport[] }>("/api/exports");
        made = listed.exports.find((e) => e.id === made.id) ?? made;
      }
      if (made.state !== "ready") throw new Error(made.error ?? "The download was not made.");
      const file = await fileOf(`/api/exports/${encodeURIComponent(made.id)}`);
      saveFile(file.blob, file.name ?? `workbench-${person.name}-data.zip`);
      setPage(await api<RemovedPeoplePage>(PATH));
    } catch (failure) {
      setProblem(`Could not export ${person.name}'s data: ${failureWords(failure)}`);
    } finally {
      setBusy(false);
    }
  }

  const later = page ? `Delete in ${page.days} days` : "Delete later";
  return (
    <section
      id="removed-people"
      aria-labelledby={`${uid}-title`}
      className="section scroll-mt-4"
      data-testid="removed-people"
    >
      <h2 id={`${uid}-title`} className="title-section">
        <UserX size={18} aria-hidden />
        Removed people
      </h2>
      <p className="help">
        {page?.retention === "none"
          ? "When you remove a person from Eugene, Workbench deletes their chats, files, notes and documents at once."
          : `When you remove a person from Eugene, Workbench hides their chats, files, notes and documents, and deletes them after ${page?.days ?? 30} days.`}
      </p>
      {page?.problem && (
        <p role="alert" className="notice notice--error">
          {page.problem}
          {page.checkedAt !== null && ` Last asked ${noteTime(page.checkedAt)}.`}
        </p>
      )}
      {page?.brake && !page.brake.dismissed && (
        <div role="alert" className="notice notice--warn" data-testid="removed-people-brake">
          <p>
            Eugene no longer knows {page.brake.count} people who use Workbench. Nothing was hidden.
            {page.brake.otherEugene && " Workbench now signs people in with a different Eugene."}
          </p>
          <p className="help">{page.brake.names.join(", ")}</p>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void load(`${PATH}/brake`, "POST", { removed: true })}
            >
              They were removed
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              disabled={busy}
              onClick={() => void load(`${PATH}/brake`, "POST", { removed: false })}
            >
              Not now
            </button>
          </div>
        </div>
      )}
      {page && page.people.length === 0 && <p className="help">Nobody has been removed.</p>}
      <ul className="flex flex-col gap-2">
        {page?.people.map((person) => (
          <li key={person.sub} className="card flex flex-col gap-2" data-testid="removed-person">
            <span>{stateWords(person)}</span>
            {person.problem && (
              <span role="alert" className="notice notice--error">
                {person.problem}
              </span>
            )}
            {confirming === person.sub ? (
              <div className="flex flex-wrap items-center gap-2" role="group">
                <span>{deleteNowWords(person.name)}</span>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={busy}
                  onClick={() => void act(person, "delete")}
                >
                  Delete now
                </button>
                <button type="button" className="btn btn-quiet" onClick={() => setConfirming(null)}>
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {person.state !== "purged" && person.state !== "purging" && (
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => void exportOf(person)}
                  >
                    Export
                  </button>
                )}
                {(person.state === "hidden" || person.state === "held") && (
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => void act(person, "keep")}
                  >
                    Keep
                  </button>
                )}
                {(person.state === "kept" || person.state === "held") && (
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => void act(person, "schedule")}
                  >
                    {later}
                  </button>
                )}
                {person.state !== "purged" && (
                  <button
                    type="button"
                    className="btn btn-danger"
                    disabled={busy}
                    onClick={() =>
                      person.state === "purging"
                        ? void act(person, "delete")
                        : setConfirming(person.sub)
                    }
                  >
                    {person.state === "purging" ? "Try again" : "Delete now"}
                  </button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="btn btn-quiet"
          disabled={busy}
          onClick={() => void load(`${PATH}/check`, "POST")}
        >
          Check now
        </button>
        {page?.checkedAt != null && (
          <span className="help">Eugene last answered {noteTime(page.checkedAt)}.</span>
        )}
      </div>
      {page && page.exportLog.length > 0 && (
        <details>
          <summary>Downloads of people's data ({page.exportLog.length})</summary>
          <ul className="help">
            {page.exportLog.map((e) => (
              <li key={e.id}>
                {e.askerName} downloaded {e.subjectName}'s data on {noteDate(e.createdAt)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
    </section>
  );
}
