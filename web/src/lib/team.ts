/**
 * Team knowledge (row 47): collections of documents and notes a team shares, who may use each,
 * and the projects shared with the person. The words around them, and the calls.
 */

import { api, del, patch, post, put } from "./api";

export type TeamRole = "manage" | "contribute" | "read";

export interface Collection {
  id: string;
  name: string;
  /** `install`: made by the owner of this Workbench; `project`: a shared project's own. */
  kind: "install" | "project";
  projectId: string | null;
  role: TeamRole;
  documents: number;
  notes: number;
  /** Notes a model proposed that wait for a contributor (0 for a reader). */
  proposed: number;
  /** Whether its passages may go to a model outside the install's machines. */
  outsideModels: boolean;
}

export interface TeamItem {
  id: string;
  kind: "document" | "note";
  state: "active" | "proposed";
  title: string;
  body?: string;
  addedBy: string;
  addedAt: number;
  formerMember: boolean;
  model?: string;
  document?: {
    state: string;
    type: string;
    bytes: number;
    passages: number;
    message: string | null;
  };
}

export interface Grant {
  kind: "person" | "group";
  id: string;
  name: string | null;
  role: "read" | "contribute";
}

export interface CollectionDetail extends Collection {
  documentList: TeamItem[];
  noteList: TeamItem[];
  inbox: TeamItem[];
  /** Who may use it: only for its manager, and only for an install collection. */
  grants: Grant[] | null;
  /** For its manager: how many of its documents other people added. */
  othersDocuments?: number;
}

export interface TeamPeople {
  people: { sub: string; name: string; username: string | null }[];
  groups: { id: string; name: string }[];
  problem: string | null;
}

export interface Sharing {
  shares: Grant[];
  collectionId: string | null;
  /** How many chats other people have in the project: a count, never their content. */
  othersChats: number;
  /** How many of its documents other people added: what deleting it takes from them. */
  othersDocuments: number;
  /** Whether its documents may go to a model outside the install's machines. */
  outsideModels: boolean;
}

export interface ChatCollections {
  chosen: { id: string; name: string }[];
  available: { id: string; name: string; role: TeamRole }[];
  /** Every collection this chat's next answer is sent, the project's included. */
  searched?: { id: string; name: string }[];
  /** Each chosen one its next answer is not sent, and why (an outside model, documents off). */
  held?: { id: string; name: string; why: string }[];
}

const at = (id: string) => `/api/collections/${encodeURIComponent(id)}`;

export const listCollections = () =>
  api<{ collections: Collection[]; canCreate: boolean }>("/api/collections");
export const readCollection = (id: string) => api<CollectionDetail>(at(id));
export const makeCollection = (name: string) => post<Collection>("/api/collections", { name });
export const setOutside = (id: string, outsideModels: boolean) =>
  patch<Collection>(at(id), { outsideModels });
export const deleteCollection = (id: string) => del<{ documents: number; notes: number }>(at(id));
export const putGrants = (id: string, grants: Omit<Grant, "name">[]) =>
  put<{ grants: Grant[] }>(`${at(id)}/grants`, { grants });
export const addNote = (id: string, note: string, body = "") =>
  post<TeamItem>(`${at(id)}/notes`, { note, body });
export const acceptNote = (id: string, item: string) =>
  post<TeamItem>(`${at(id)}/inbox/${encodeURIComponent(item)}/accept`, {});
export const removeItem = (id: string, item: string) =>
  del(`${at(id)}/items/${encodeURIComponent(item)}`);
export const teamPeople = () => api<TeamPeople>("/api/team/people");
export const putSharing = (projectId: string, shares: Omit<Grant, "name">[]) =>
  put<Sharing>(`/api/projects/${encodeURIComponent(projectId)}/sharing`, { shares });
export const chatCollections = (chatId: string) =>
  api<ChatCollections>(`/api/chats/${encodeURIComponent(chatId)}/collections`);
export const putChatCollections = (chatId: string, ids: string[]) =>
  put<ChatCollections>(`/api/chats/${encodeURIComponent(chatId)}/collections`, { ids });
export const projectCollections = (projectId: string) =>
  api<ChatCollections>(`/api/projects/${encodeURIComponent(projectId)}/collections`);
export const putProjectCollections = (projectId: string, ids: string[]) =>
  put<ChatCollections>(`/api/projects/${encodeURIComponent(projectId)}/collections`, { ids });

/** What a role lets a person do, in one line. */
export function roleWords(role: TeamRole): string {
  switch (role) {
    case "manage":
      return "You manage it";
    case "contribute":
      return "You can add to it";
    case "read":
      return "You can read it";
  }
}

/** Who added an item, as a line says it. */
export function addedWords(item: TeamItem): string {
  return item.formerMember ? "Added by a former member" : `Added by ${item.addedBy}`;
}
