/**
 * A removed person's data (row 7): what the server says of people Eugene no longer has, and
 * of downloads of people's data, and the words the page uses for them.
 */

import { noteDate, plural } from "./memory";

export type RemovedState = "held" | "hidden" | "kept" | "purging" | "purged";

export interface RemovedPerson {
  sub: string;
  name: string;
  state: RemovedState;
  removedAt: number;
  /** When their data is deleted, worked out from the setting in effect; null unless hidden. */
  deleteAt: number | null;
  purgedAt: number | null;
  problem: string | null;
}

export interface DataExport {
  id: string;
  subject: string;
  subjectName: string;
  askerName: string;
  scope: "self" | "owner";
  state: "making" | "ready" | "failed" | "expired" | "deleted";
  holds: Record<string, number>;
  size: number | null;
  error: string | null;
  createdAt: number;
  expiresAt: number;
}

export interface RemovedPeoplePage {
  /** `days`: kept hidden for `days`, then deleted; `none`: deleted at once. */
  retention: "days" | "none";
  days: number;
  people: RemovedPerson[];
  brake: { count: number; names: string[]; dismissed: boolean; otherEugene: boolean } | null;
  checkedAt: number | null;
  problem: string | null;
  exportLog: DataExport[];
}

/** The line that says where a removed person's data stands. */
export function stateWords(person: RemovedPerson): string {
  const removed = `removed ${noteDate(person.removedAt)}`;
  switch (person.state) {
    case "hidden":
      return `${person.name} · ${removed} · deleted on ${noteDate(person.deleteAt ?? 0)}`;
    case "kept":
      return `${person.name} · kept until you delete it`;
    case "held":
      return `${person.name} · removed before this update · nothing is deleted until you choose`;
    case "purging":
      return `${person.name} · ${removed} · deleting now`;
    case "purged":
      return `${person.name} · data deleted on ${noteDate(person.purgedAt ?? person.removedAt)}`;
  }
}

/** The owner's line above every page: what is about to be deleted, or what waits for them. */
export function noticeWords(page: RemovedPeoplePage): string | null {
  if (page.brake && !page.brake.dismissed) {
    return `Eugene no longer knows ${page.brake.count} people who use Workbench. Nothing was hidden.`;
  }
  const dated = page.people.filter((p) => p.state === "hidden" && p.deleteAt !== null);
  if (dated.length === 0) return null;
  const soonest = Math.min(...dated.map((p) => p.deleteAt as number));
  const whose = plural(dated.length, "removed person's", "removed people's");
  return `${whose} data will be deleted on ${noteDate(soonest)}.`;
}

/** "Delete Jessie's chats, files, notes and documents now? This cannot be undone." */
export function deleteNowWords(name: string): string {
  return `Delete ${name}'s chats, files, notes and documents now? This cannot be undone.`;
}

/** A download's size, as a person reads it. */
export function sizeWords(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
