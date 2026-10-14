/** What Workbench's own API answers with (src/eugene_plexus_workbench/api.py). */

import type { ArtifactSummary } from "./artifacts";

export type MessageStatus = "done" | "running" | "stopped" | "interrupted" | "failed";

/** What a web search found and the answer linked. */
export interface WebSource {
  kind: "web";
  url: string;
  title: string;
  /** Where the answer cites it, as [start, end) in the message's content,
   * counted as the page counts (rich content RC4). Absent when the gateway
   * gave no place. */
  marks?: [number, number][];
}

/** A document that was sent to the model with the message (MR2). It lists what
 * was SENT, never what the model used: `partial` is the first part only, `native`
 * the file itself (a model that reads PDFs), `replayed` a file attached earlier
 * in the chat and sent again. The server counts pages and lines from 1. */
export interface DocumentSource {
  kind: "document";
  doc_id: string;
  name: string;
  type: string | null;
  native: boolean;
  partial: boolean;
  tokens: number | null;
  replayed: boolean;
  page_start: number | null;
  page_end: number | null;
  pages_total: number | null;
  line_start: number | null;
  line_end: number | null;
  lines_total: number | null;
  /** How much of the text went, 0 to 1; only on a partial read. */
  fraction?: number;
}

/** A passage of a person's saved documents that was found for their question and
 * sent to the model with the message (MR4). It lists what was SENT, never what the
 * model used: `cited` is only whether the answer's own text has this passage's
 * number in it, like [2], and is absent until the answer is done. `snippet` is at
 * most 240 characters, around the words that were searched for. */
export interface PassageSource {
  kind: "document";
  passage: true;
  doc_id: string;
  name: string;
  type: string | null;
  scope: "library" | "chat";
  /** 1, 2, 3 ...: the number the model was asked to cite. */
  rank: number;
  via: Via;
  page_start: number | null;
  page_end: number | null;
  line_start: number | null;
  line_end: number | null;
  heading: string | null;
  snippet: string;
  chunk_ids: number[];
  tokens: number;
  terms: string[];
  phrases: string[];
  cited?: boolean;
}

/** A passage as the install's owner sees it in a chat they may read: that one was
 * sent, and nothing of which (saved documents are never the owner's to read). */
export interface HiddenPassage {
  kind: "document";
  passage: true;
  name: "a document";
  scope: "library" | "chat" | null;
  rank: number | null;
  via: Via | null;
  redacted: true;
}

export type Source = WebSource | DocumentSource | PassageSource | HiddenPassage;

/** A file a message carried, as chat detail lists it. `reading` is how Workbench
 * read it, for a document; null for an image or audio. */
export interface AttachedFile {
  id: string;
  name: string;
  mediaType: string;
  reading?: Reading | null;
}

export interface Message {
  id: string;
  seq: number;
  role: "user" | "assistant";
  content: string;
  reasoning: string;
  attachments: string[];
  files?: AttachedFile[];
  status: MessageStatus;
  error: string | null;
  sources: Source[];
  searches: number;
  /** A search provider's required Search Suggestions: Google's own HTML, one
   * per search that had any, shown as it came and nowhere else (GS4). */
  searchSuggestions?: string[];
  search: boolean;
  model: string | null;
  finish: string | null;
  createdAt: number;
  finishedAt: number | null;
  /** Where the text after the reply's last web search begins, in the page's
   * string length; null when no search marked one (workbench#1). */
  answerFrom?: number | null;
  reasoningFrom?: number | null;
  toolRounds?: { calls: ToolCall[] }[];
  /** The owner reading someone's chat in production mode: from the first
   * job-site result on, nothing but which machine it used (J13a). */
  redacted?: { site: string };
  /** Its place among its versions, from 1: the edits of a message, or the
   * tries of an answer, oldest first (workbench-answer-versions.md). */
  versions?: Versions;
  /** What the blocks the person changed keep (rich content RC5), found again by
   * each block's words (lib/artifacts.ts `digest`). */
  artifacts?: ArtifactSummary[];
  /** The output cap the gateway sent for this answer and where it came
   * from; null when it sent none or did not say (2026-10-10). */
  outputCap?: OutputCap | null;
  /** What searching the person's saved documents came to for this answer, or
   * null when nothing was searched (MR4). */
  retrieval?: SearchRecord | null;
}

export interface OutputCap {
  tokens: number;
  source: "request" | "profile" | "install";
}

export interface Versions {
  index: number;
  count: number;
  ids: string[];
}

export interface ToolCall {
  id: string;
  serverId: string;
  serverName: string;
  tool: string;
  arguments: Record<string, unknown>;
  status:
    "pending" | "signing" | "running" | "done" | "declined" | "cancelled" | "failed" | "uncertain";
  result: string | null;
  jobSite?: boolean;
  site?: string | null;
  label?: string | null;
  mode?: "production" | "dev";
  /** Asked about here; false when the site's rules allow it without asking
   * (2b.3b, J70). Absent from a call made before then: it was asked. */
  ask?: boolean;
  /** The site gave rules for it, so it is told the person's word (J72). */
  rules?: boolean;
  /** The site checks your own signature on it (J14b): it holds the call and
   * says what to sign, rather than Workbench asking first. */
  signed?: boolean;
  /** While `signing`: what the site holds, in its own words. */
  held?: HeldCall;
  /** When your open window on that site ended, as of this call. */
  windowUntil?: string;
  /** What the call returned, in order (rich content RC3); `result` is the
   * same as the words the model is sent. Absent from a call made before. */
  content?: ContentBlock[];
  contentVersion?: number;
}

/** A file a tool returned, kept as its chat's own. */
export interface ToolFile {
  id: string;
  type: "image" | "audio" | "video" | "file";
  fileId: string;
  name: string;
  mediaType: string;
  size: number;
  width?: number;
  height?: number;
  /** What a file said it was, when its bytes did not bear it out. */
  declaredType?: string;
}

/** One block of a tool's result (rich content RC3). */
export type ContentBlock =
  | { id: string; type: "text"; text: string; source?: string }
  | ToolFile
  | { id: string; type: "data"; data: unknown }
  | {
      id: string;
      type: "link";
      uri: string;
      name: string;
      title?: string;
      description?: string;
      mimeType?: string;
    }
  | { id: string; type: "unsupported"; what: string; reason: string };

/** A call a site holds for your signature (`SiteHeldCall`, J86). */
export interface HeldCall {
  id: string;
  /** `call`: this call. `window`: a window in which the tools your rules
   * allow run without your signature each. */
  kind: "call" | "window";
  minutes?: number | null;
  words: string[];
  expiresAt?: string;
  /** The machine's own page, where you can sign it and see exactly what it is. */
  approvePage?: string | null;
  message?: string;
}

interface ServerIdentity {
  id: string;
  name: string;
}

export type ToolServer = ServerIdentity &
  (
    | {
        transport: "http";
        url: string;
        hasToken: boolean;
      }
    | {
        transport: "stdio";
        command: string;
        args: string[];
        environmentKeys: string[];
        access: "owner";
      }
    | ({
        /** A local server on one of the person's job sites, reached through Eugene. */
        transport: "site";
        site: string;
        label: string;
        server: string;
        available: boolean;
        reason: string | null;
        jobSite: boolean;
      } & SiteLinking)
  );

export interface ToolServers {
  servers: ToolServer[];
  localProcesses: { available: boolean; reason: string | null };
}

export interface ChatSettings {
  toolServers?: string[] | null;
  folderGrants?: string[] | null;
  instructions?: string | null;
  temperature?: number | null;
  topP?: number | null;
  maxTokens?: number | null;
  repetitionMode?: "off" | "observe" | "stop" | null;
  /** Memory in this chat: `off` sends no notes to its model; unset follows
   * the person's own setting (MR1). */
  memory?: "on" | "off" | null;
  /** The person chose "Send for this chat": notes go to this chat's model
   * though it runs outside their machines. */
  memoryExternal?: true | null;
  /** Documents in this chat: `on` searches the person's saved documents for each
   * question; unset is off (MR4). */
  documents?: "on" | "off" | null;
  /** Answer only from the person's documents: when nothing matches, Workbench
   * says so itself and does not ask the model. */
  documentsOnly?: boolean | null;
  /** The person chose "Send for this chat" for passages of their documents. */
  documentsExternal?: true | null;
}

export interface FolderGrant {
  id: string;
  name: string;
  subject: string;
  writable: boolean;
  usable: boolean;
  path?: string;
  source?: "local" | "node";
  site?: string;
  label?: string;
  available?: boolean;
  reason?: string | null;
  linked?: boolean;
  account?: string;
  linkPage?: string;
}

export interface FolderGrants {
  grants: FolderGrant[];
  available: boolean;
  reason: string | null;
  localAvailable?: boolean;
  nodeReason?: string | null;
  manageUrl?: string | null;
}

export interface Chat {
  id: string;
  title: string;
  model: string | null;
  search: boolean;
  settings: ChatSettings;
  createdAt: number;
  updatedAt: number;
  running: boolean;
  readOnly: boolean;
}

export interface ChatDetail {
  chat: Chat;
  messages: Message[];
  ownerName: string | null;
}

export interface Me {
  sub: string;
  name: string;
  username: string | null;
  owner: boolean;
  ownerReadsChats: boolean;
  consoleUrl?: string | null;
  installMode?: "production" | "dev";
  installModeChangedAt?: string | null;
  installModeNotice?: boolean;
  /** Whether the person's artifact previews run when shown (RC6); on unless turned off. */
  runPreviews?: boolean;
}

/** What a rule says of a group of tools (J70). */
export type Decision = "allow" | "ask" | "deny";

export interface SiteRules {
  read: Decision;
  change: Decision;
  /** Running commands (2b.4, J88): each one needs your own signature, or
   * never. Absent from a workspace made before commands: never. */
  command?: "ask" | "deny" | null;
}

export interface WorkspacePerson {
  person: string;
  name: string;
  read: Decision;
  change: Decision;
}

/** One of a person's own workspaces, by id and name (J76). */
export interface JobSiteWorkspace {
  id: string;
  name: string;
  writable: boolean;
  rules: SiteRules;
  people: WorkspacePerson[];
}

/** The same, read live from the machine: with its path and hidden patterns. */
export interface JobSiteWorkspaceDetail extends JobSiteWorkspace {
  path: string;
  deny: string[];
}

export interface SiteTool {
  name: string;
  title?: string | null;
  description?: string | null;
  readOnly: boolean;
  destructive: boolean;
}

export interface SiteServer {
  id: string;
  name: string;
  kind: "files" | "local";
  system: boolean;
  enabled: boolean;
  available: boolean;
  reason: string | null;
  tools: SiteTool[];
}

export interface JobSiteServer {
  server: SiteServer;
  people: {
    person: string;
    name: string;
    tools: { name: string; decision?: "allow" | "ask" | null; standing?: boolean }[];
  }[];
}

export interface SiteAuditEntry {
  at: string;
  subject: string;
  kind: "mcp" | "manage";
  server?: string | null;
  method?: string | null;
  tool?: string | null;
  action?: string | null;
  arguments?: string | null;
  decision: "allowed" | "refused";
  outcome?: string | null;
  reason?: string | null;
}

export interface JobSite {
  /** The site's own id (`s-` and 26 characters), never its label. */
  id: string;
  /** `owner`: yours. `linked`: you linked your account on it (2b.3b), and
   * see only your own there. */
  role: "owner" | "linked";
  /** Your own workspaces there (2b.3b), by id and name. */
  workspaces?: JobSiteWorkspace[];
  /** The machine's name; not unique. */
  label: string;
  hostNode?: string | null;
  online: boolean;
  ready: boolean;
  reason: string | null;
  account: string | null;
  lastContactAt: string | null;
  /** The local servers its administrator added at the machine. */
  servers?: JobSiteServer[];
  /** Whether Eugene's owner may use folders here while Eugene is in developer mode (J6e). */
  ownerInDevMode?: boolean | null;
  /** Who has linked their own OS account on the machine (no person names). */
  links?: SiteLink[];
  /** Where people link at the machine; null when they link another way. */
  linkPage?: string | null;
  /** False where only the machine's owner is served (macOS). */
  sharing?: boolean;
  /** Whether the machine checks its owner's changes with the owner's own key
   * (J14a). Absent until the machine has reported. */
  signing?: SiteSigning;
  /** Whether the machine runs commands from Workbench (2b.4, J89). Absent
   * until the machine has reported. */
  commands?: SiteCommands;
}

export interface SiteCommands {
  allowed: boolean;
  consentedAt?: string | null;
  withdrawnAt?: string | null;
  /** Why not, and how an administrator allows them at the machine. */
  reason?: string | null;
}

export interface SiteSigning {
  /** `unsigned`: no key yet, so no tool runs there. `unconfirmed`: a key, but
   * its rules are not approved with it yet. `signed`: tools run. */
  state: "unsigned" | "unconfirmed" | "signed";
  /** Changes the machine holds until they are approved there. */
  held: number;
  /** Where to add a key and approve changes, at the machine. */
  approvePage?: string | null;
  /** The machine takes a passkey from here, paired with a code it shows (J14a.3). */
  passkeys?: boolean;
}

/** What this Workbench needs to make a passkey (J14a.3): its relying party,
 * null where it has no https address, and who is signed in. */
export interface PasskeyContext {
  rpId: string | null;
  person: string;
  name: string | null;
}

export interface SitePasskey {
  id: string;
  credentialId: string;
  alg: number;
  rpId: string;
  label: string;
  addedAt: string;
}

export interface HeldItem {
  id: string;
  action: string;
  words: string[];
  heldAt: string | null;
  expiresAt?: string | null;
  envelope?: string | null;
}

export interface HeldList {
  subject: string;
  keys: string[];
  passkeys?: SitePasskey[];
  state?: "unsigned" | "unconfirmed" | "signed" | null;
  items: HeldItem[];
}

/** A change a machine holds until its owner approves it there (J14a). */
export interface HeldChange {
  held: true;
  message: string;
}

export interface SiteLink {
  subject: string;
  accountName: string;
  available: boolean;
  reason?: string | null;
  /** How many keys this person has pinned at the machine. */
  keys?: number;
  /** The state of this person's own rules there (2b.3b). */
  signing?: "unsigned" | "unconfirmed" | "signed" | null;
  /** Their changes waiting for their own key. */
  held?: number;
  /** When their open window there ends (J14b, J81); null when none is open. */
  windowUntil?: string | null;
}

/** What Eugene says about whose account a person's calls run as on a machine.
 * Nothing for Eugene's own owner, who links no account. */
export interface SiteLinking {
  linked?: boolean;
  account?: string;
  linkPage?: string;
}

export interface JobSiteList {
  sites: JobSite[];
  canInvite: boolean;
  passkeys?: PasskeyContext;
}

export interface JobSiteInvite {
  expiresAt: string;
  label: string | null;
  commands: { windows: string; posix: string };
}

export interface Model {
  id: string;
  contextLength: number | null;
  imageInput: boolean;
  audioInput: boolean;
  fileInput: boolean;
  webSearch: boolean;
  ready: boolean;
  onDemand: boolean;
  /** Where it runs, as the gateway says: `unknown` is not trusted with notes. */
  locality?: "local" | "external" | "unknown";
}

export interface SearchAvailability {
  available: boolean;
  reason: string | null;
}

export interface Models {
  models: Model[];
  webSearch: SearchAvailability;
}

export interface Progress {
  stage: "prompt" | "working" | "tool" | "generating" | "documents";
  tool?: string;
  /** On `tool`: whether the call started, finished or waits for approval. On
   * `documents`: `reading`, while the answer waits for its files to be read, or
   * `searching`, while the person's saved documents are searched. */
  phase?: "started" | "finished" | "approval" | "reading" | "searching";
  /** On `documents`: how many files are being read. */
  files?: number;
  prompt_tokens?: number;
  cached_tokens?: number;
  processed_tokens?: number;
  /** On `generating`: tokens written so far, thinking included, as the
   * backend counted them. Only a backend that counts as it writes sends it. */
  generated_tokens?: number;
  tokens_per_second?: number;
}

export type ChatEvent =
  | { type: "answer"; message: Message; progress: Progress | null }
  | {
      type: "delta";
      id: string;
      content?: string;
      contentAt?: number;
      reasoning?: string;
      reasoningAt?: number;
      sources?: Source[];
      searchSuggestions?: string[];
      retrieval?: SearchRecord | null;
    }
  | {
      type: "progress";
      id: string;
      progress: Progress;
      /** Sent when a web search starts or finishes: the answer begins here. */
      answerFrom?: number;
      reasoningFrom?: number;
    }
  | { type: "done"; message: Message }
  | { type: "reload" }
  /** Another version is shown: Try again, an edit, or a choice. */
  | { type: "path" }
  | { type: "signed-out"; reason: string; message: string };

export interface Person {
  sub: string;
  name: string;
  chats: number;
  /** How many media results they have (M3). */
  media: number;
}

// --- Memory (MR1): what src/eugene_plexus_workbench/memory_api.py answers with ---

export type NoteKind = "fact" | "preference" | "instruction" | "project" | "event";

/** How a note got here: written on the Memory page, said in a chat
 * ("Remember that ..."), or kept from a message's Remember this. */
export type NoteSource = "typed" | "said" | "button";

export interface MemoryNote {
  id: string;
  text: string;
  kind: NoteKind;
  pinned: boolean;
  /** Pinned, or an instruction: used first, in every chat. */
  always: boolean;
  source: NoteSource;
  sourceChatId: string | null;
  sourceMessageId: string | null;
  /** Epoch seconds, as everywhere in Workbench's API. */
  createdAt: number;
  updatedAt: number;
  lastUsedAt: number | null;
  useCount: number;
}

/** How far notes may travel to a model outside the person's machines. */
export type ExternalChoice = "never" | "ask" | "always";

/** What a person has set (`null`: not chosen) and what is in effect, with whose
 * word it is. The page shows `effective`, never a stand-in that looks chosen. */
export interface MemoryPrefs {
  enabled: boolean;
  external: ExternalChoice | null;
  budgetTokens: number | null;
  effective: {
    enabled: boolean;
    disabledBy: "owner" | "you" | null;
    external: ExternalChoice;
    externalSetBy: "owner" | "you" | "default";
    budgetTokens: number;
  };
}

/** `GET /api/memory`: the Memory page. */
export interface MemoryPage {
  installEnabled: boolean;
  ownerReadsChats: boolean;
  prefs: MemoryPrefs;
  sections: { always: MemoryNote[]; other: MemoryNote[] };
  usage: {
    tokens: number;
    budget: number;
    notes: number;
    maxNotes: number;
    sent: number;
    notSent: number;
  };
  limits: {
    maxChars: number;
    maxNotes: number;
    kinds: NoteKind[];
    minWindow: number;
    budgetCeiling: number;
    budgetSharePercent: number;
    budgetFloor: number;
  };
}

/** Why a chat's next request carries no notes, in the order a person can act on it. */
export type MemoryReason =
  | "install_off"
  | "person_off"
  | "chat_off"
  | "no_model"
  | "model_unknown"
  | "window_too_small"
  | "outside_model"
  | "no_notes";

export interface SentNote {
  id: string;
  text: string;
  kind: NoteKind;
  pinned: boolean;
  always: boolean;
}

export interface NotSentNote extends MemoryNote {
  reason: "over_budget" | "added_since";
  reasonText: string;
}

/** `GET /api/chats/{id}/memory`, for a chat that is the asker's. */
export interface ChatMemory {
  chatId: string;
  readOnly: false;
  sending: boolean;
  reason: string | null;
  reasonCode: MemoryReason | null;
  hint: string | null;
  chatSwitch: "on" | "off";
  model: {
    id: string;
    window: number | null;
    locality: "local" | "external" | "unknown";
    runsOn: string | null;
  } | null;
  outside: {
    choice: ExternalChoice;
    setBy: "owner" | "you" | "default";
    canSendForThisChat: boolean;
    sendForThisChat: boolean;
  } | null;
  counts: { notes: number; sent: number; notSent: number };
  frozen: boolean;
  frozenAt: number | null;
  /** Notes edited, added or re-pinned since the chat took its copy. */
  changed: boolean;
  budget: number | null;
  tokens: number;
  /** The exact block the model was given, or null. */
  text: string | null;
  sent: SentNote[];
  notSent: NotSentNote[];
}

/** The install owner reading someone else's chat: counts, never the notes. */
export interface ChatMemoryReadOnly {
  chatId: string;
  readOnly: true;
  frozen: boolean;
  frozenAt: number | null;
  tokens: number;
  budget: number | null;
  counts: { sent: number; notSent: number };
}

export type ChatMemoryView = ChatMemory | ChatMemoryReadOnly;

/** Why words were not kept: the server's sentence, and what it saw. */
export interface Refusal {
  message: string;
  reason?: "empty" | "hidden" | "secret" | "too_long" | "full" | "off" | "error";
  /** For a secret: what it has the shape of ("a card number"). */
  looksLike?: string;
  limit?: number;
}

/** What a message that began with "remember" or "forget" did to the notes. */
export type MemoryCommand =
  | { command: "remember"; remembered: MemoryNote; created: boolean }
  | { command: "remember"; refused: Refusal }
  | { command: "forget"; words: string; candidates: MemoryNote[] };

// --- Documents (MR2): what src/eugene_plexus_workbench/documents_api.py answers with ---

export type ReadingState = "reading" | "ready" | "skipped" | "failed";
export type ReadingKind = "pdf" | "docx" | "markdown" | "csv" | "html" | "code" | "text";

/** How much of a document to read: all of it, or its first part, up to a number
 * of tokens, or none of it: "Search it instead" keeps the file as a document of
 * this chat, and each question is sent the passages that match it (MR4). The
 * choice is kept with the file. */
export type ReadingChoice =
  { mode: "whole" } | { mode: "first"; tokens: number } | { mode: "search" };

/** How reading a file went (`context.reading_view`). The numbers are the ones
 * Workbench counted; `message` is the sentence to show for a file it skipped or
 * could not read. */
export interface Reading {
  state: ReadingState;
  kind: ReadingKind | null;
  /** PDF, Word, Markdown, CSV, HTML, code or text. */
  label: string | null;
  language: string | null;
  pages: number | null;
  lines: number | null;
  tokens: number | null;
  chars: number | null;
  headings: number | null;
  notes: string[];
  truncated: boolean;
  code: string | null;
  message: string | null;
  chosen: ReadingChoice | null;
  startedAt: number | null;
  finishedAt: number | null;
}

/** Which part of a file goes, as the server counts it. */
export interface Coverage {
  whole: boolean;
  tokens: number;
  /** `pages 1 to 9 of 30`, `lines 1 to 567 of 900`, `3 pages`, `all of it`. */
  where: string;
  pages: [number, number] | null;
  totalPages: number | null;
  partialPage: boolean;
  lines: [number, number] | null;
  totalLines: number | null;
  fraction: number;
  heading: string | null;
}

/** What a person may choose for a file that is not read whole by default. A
 * file may always be searched instead (the offer that ends the list). */
export type Offer =
  | { action: "readAll"; tokens: number; where: string }
  | { action: "readFirst"; tokens: number; where: string; cut?: Coverage }
  | { action: "search" };

export type DecisionKind =
  | "pending"
  | "whole"
  | "partial"
  | "native"
  | "search"
  | "choose"
  | "too_long"
  | "no_room"
  | "unreadable";

/** How the document made of a file the person chose to search instead is getting
 * on (`documents_api._search_view`). */
export interface FileSearch {
  docId: string | null;
  status: SavedState;
  message: string | null;
  passages: number;
}

/** What a request to a model would do with one file (`context.Decision.view`).
 * `send` is whether it can go as things stand. */
export interface FileDecision {
  kind: DecisionKind;
  send: boolean;
  message: string;
  tokens: number | null;
  blockTokens: number;
  native: boolean;
  replayed: boolean;
  chosen: ReadingChoice | null;
  /** A choice that was kept and no longer fits this model, so it is not used. */
  choiceNoLongerFits: boolean;
  offers: Offer[];
  coverage: Coverage | null;
  notes: string[];
  window: number | null;
  wholeLimit: number;
  readAllLimit: number | null;
  overhead?: number;
  /** Only on a file the person chose to search instead: how its own document is
   * getting on, or null before it has one. */
  search?: FileSearch | null;
  room: {
    history: number;
    memory: number;
    documents: number;
    reserve: number;
    remaining: number | null;
  };
}

/** One of a chat's files while the message is being written. Everything but the
 * first three is absent from a file the page was handed without asking (an
 * image sent from the media area), and `reading` and `decision` are null for an
 * image or audio. */
export interface ChatFile {
  id: string;
  name: string;
  mediaType: string;
  size?: number;
  createdAt?: number;
  kind?: "document" | "image" | "audio";
  /** Already sent with a message, so no longer this message's to carry. */
  sent?: boolean;
  reading?: Reading | null;
  decision?: FileDecision | null;
  /** For a file the person chose to search instead: how its own document is
   * getting on; null for any other. */
  search?: FileSearch | null;
}

export interface FilesModel {
  id: string | null;
  /** Whether Eugene said how large the model's window is. */
  known: boolean;
  window: number | null;
  fileInput: boolean;
}

export interface FilesRequest {
  used: number;
  window: number | null;
  reserve: number;
  /** Why the request as a whole cannot go, when every file fits alone. */
  full: string | null;
}

/** `GET /api/chats/{id}/files?model=`: the decisions for the model the person picked. */
export interface ChatFiles {
  model: FilesModel;
  files: ChatFile[];
  request: FilesRequest | null;
}

// --- Saved documents (MR4): what src/eugene_plexus_workbench/saved_api.py answers with ---

/** Where a saved document stands. `embedding` is held for search by meaning. */
export type SavedState =
  "queued" | "reading" | "searchable" | "embedding" | "ready" | "skipped" | "failed";

/** How much of one document's meaning is added, and what is true of it now
 * (`Meaning.document_view`, MR5): `ready` (every passage has its meaning, or was
 * refused it), `words_only` (read while no search model was running), `adding`,
 * `waiting` (the search model is not there, or is not the one this search was built
 * with) or `limited` (the key is at its limit). `message` is the server's sentence
 * for the three that are in the middle of something, as it came; `refused` counts
 * the passages the search model would not take, and `notes` say each one's why. */
export type MeaningState = "ready" | "words_only" | "adding" | "waiting" | "limited";

export interface DocumentMeaning {
  embedded: number;
  total: number;
  etaSeconds: number | null;
  refused: number;
  notes: string[];
  state: MeaningState;
  message: string | null;
}

/** One of a person's saved documents (`saved_api.document_view`). `message` is the
 * sentence for a document that was skipped or could not be read, as it came. `meaning`
 * is null until the document is read, and for one that could not be. */
export interface SavedDocument {
  id: string;
  name: string;
  mediaType: string;
  kind: string;
  label: string | null;
  language: string | null;
  scope: "library" | "chat";
  size: number;
  pages: number | null;
  lines: number | null;
  tokens: number | null;
  state: SavedState;
  searchable: boolean;
  message: string | null;
  code: string | null;
  passages: number;
  embedded: number;
  notes: string[];
  /** Epoch seconds, as everywhere in Workbench's API. */
  addedAt: number | null;
  readAt: number | null;
  indexedAt: number | null;
  meaning: DocumentMeaning | null;
}

/** A document just added: whether it was already there byte for byte, and the ones
 * a file of the same name replaced. */
export interface AddedDocument extends SavedDocument {
  unchanged: boolean;
  replaced: string[];
}

/** A file that was not added, and why, in the server's words. */
export interface AddRefusal {
  name: string;
  status: number;
  message: string;
}

export interface AddedFiles {
  documents: AddedDocument[];
  refused: AddRefusal[];
}

/** Why documents are searched as they are (`Status.code`, MR5): `ok` is meaning and
 * words; every other one is why meaning is not in use. `no_gateway` is a Workbench with
 * no gateway to ask about a search model at all. */
export type SearchCode =
  | "ok"
  | "none_set_up"
  | "not_running"
  | "not_listed"
  | "not_search_model"
  | "outside"
  | "outside_only"
  | "unreachable"
  | "key"
  | "limited"
  | "slow"
  | "refused"
  | "error"
  | "changed"
  | "dimension"
  | "damaged"
  | "no_gateway";

/** What a person can do about it: make the search again (`rebuild`), or get a search
 * model, which is Eugene's console's to do (`get_search_model`). */
export type SearchAction = "rebuild" | "get_search_model";

/** How meaning is searched when the search is by meaning at all: whether the
 * prefixes the model wants on a question and a passage are known. */
export interface SearchPrefixes {
  known: boolean | null;
  family: string | null;
  query: string | null;
  document: string | null;
  /** The sentence that says they are not known, or null. */
  note: string | null;
}

/** The search model in use and what the search was built with (`Meaning.status`). `label`
 * is the sentence to show, "In use: X (automatic)"; `locality` and `runsOn` say where it
 * runs, and `allowExternal` whether the owner lets text go to a model outside the machines. */
export interface SearchModelView {
  setting: string;
  automatic: boolean;
  resolved: string | null;
  label: string | null;
  locality: "local" | "external" | "unknown" | null;
  runsOn: string | null;
  allowExternal: boolean;
  outside: string[];
  builtWith: string | null;
  dimension: number | null;
  prefixes: SearchPrefixes;
  floor: number | null;
}

/** How adding meaning is going for the person's documents: `working`, `waiting` (the
 * search model is not there), `limited` (the key is at its limit) or `idle`. */
export interface AddingView {
  state: "idle" | "working" | "waiting" | "limited";
  message: string | null;
  passages: number;
  embedded: number;
  owed: number;
  wordsOnly: number;
  etaSeconds: number | null;
  ratePerSecond: number | null;
}

/** What Rebuild would do: add meaning again to this many passages with this search model,
 * and how long that takes when it is known. */
export interface RebuildView {
  passages: number;
  seconds: number | null;
  model: string | null;
}

/** The ways documents are searched, as the server tells a mode: `none` is a search none of
 * whose ways could run. */
export type SearchMode = "words" | "meaning" | "meaning and words" | "none";

/** A mode as the server says it when a result sorter put the best passages in order (MR6b):
 * how documents were searched, then `, sorted by <the sorter>`: `meaning and words, sorted by
 * Qwen3-Reranker`. Every other mode is one of `SearchMode`. */
export type SortedMode = `${Exclude<SearchMode, "none">}, sorted by ${string}`;

/** Every code the result sorter can say, on the Documents screen (`SortFacts.code`) and for a
 * question (`SortRun.code`, which is null for a question that was sorted). `ok` is only the
 * screen's: a sort of the usual size is quick enough. `measuring` is why a question was not
 * sorted while the sorter's speed was not known yet (it is measured behind the question, which
 * never waits for it), and the screen's while that lasts. The rest are why passages were not
 * sorted: `off` and `none_set_up` (never in play), `not_running`, `not_listed`, `not_sorter`,
 * `outside` and `outside_only` (which sorter), `over_budget` (too slow for Sort time: a measured
 * speed) and `slow` (it did not answer in time, which is not a speed: no `ratePerSecond`),
 * `limited`, `key`, `unreachable`, `refused`, `unusable` and `error` (what the sorter or the
 * gateway said), and the question's own: `nothing_to_sort`, `no_question`, `not_asked` (also a
 * question that named its way, which is not sorted) and `none`. */
export type SortCode =
  | "ok"
  | "measuring"
  | "over_budget"
  | "off"
  | "none_set_up"
  | "not_running"
  | "not_listed"
  | "not_sorter"
  | "outside"
  | "outside_only"
  | "limited"
  | "key"
  | "unreachable"
  | "slow"
  | "refused"
  | "unusable"
  | "error"
  | "nothing_to_sort"
  | "no_question"
  | "not_asked"
  | "none";

/** The result sorter right now (`sorting.Standing.view`, from a live look and what was
 * measured): which one, how fast it reads, the time a sort may add to an answer, and whether a
 * sort of the usual size is quick enough (`sorting`), or why not (`code` and `message`, in the
 * server's words, with the numbers). `typical` is that usual sort. `quiet` is a sorter that was
 * never in play, which is not worth saying beside every answer. */
export interface SortFacts {
  /** The Result sorter setting as it is: `auto`, `off`, or the id the owner named. */
  setting: string;
  automatic: boolean;
  sorting: boolean;
  code: SortCode;
  message: string | null;
  quiet: boolean;
  model: string | null;
  named: string | null;
  /** "In use: X (automatic)", said only while a sort of the usual size is made (`sorting`): a
   * sorter that is chosen and not in use has none. */
  label: string | null;
  locality: "local" | "external" | "unknown" | null;
  runsOn: string | null;
  allowExternal: boolean;
  outside: string[];
  /** The Sort time setting: how long a sort may be expected to take (one that is asked is given
   * up on after twice this). */
  budgetSeconds: number;
  /** How fast it reads, as it was measured; null while nothing is measured and while it is only
   * known that it did not answer (`slow`), which is not a speed. `rateFrom` is what that mostly
   * comes from: `probe` while quick tests of the sorter read more than half of what it is made of,
   * `sorts` after. */
  ratePerSecond: number | null;
  rateFrom: "probe" | "sorts" | null;
  typical: { passages: number; passageTokens: number; tokens: number; seconds: number | null };
}

/** How documents are searched right now (`saved_api.search_facts`, from a live look):
 * `headline` is the sentence to show, `detail` says why in the code's own words, and
 * `actions` what the person can do about it. `sort` is the result sorter. */
export interface SearchFacts {
  mode: "words" | "meaning and words" | SortedMode;
  words: boolean;
  meaning: boolean;
  headline: string;
  code: SearchCode;
  detail: string | null;
  actions: SearchAction[];
  model: SearchModelView | null;
  adding: AddingView | null;
  rebuild: RebuildView | null;
  sort?: SortFacts | null;
}

/** What a person has set (`null`: not chosen) and what is in effect, with whose
 * word it is. The page shows `effective`, never a stand-in that looks chosen. */
export interface DocumentsPrefs {
  newChats: boolean;
  external: ExternalChoice | null;
  effective: {
    enabled: boolean;
    disabledBy: "owner" | null;
    newChats: boolean;
    external: ExternalChoice;
    externalSetBy: "owner" | "you" | "default";
  };
}

export interface DocumentCounts {
  documents: number;
  passages: number;
  waiting: number;
}

/** `GET /api/documents`: the Documents screen. */
export interface DocumentsPage {
  documents: SavedDocument[];
  totals: {
    documents: number;
    passages: number;
    bytes: number;
    byState: Record<string, number>;
    passagesKept: number;
    /** Passages that have their meaning (or were refused it), and passages still owed it. */
    embedded: number;
    owed: number;
  };
  limits: {
    maxFileBytes: number;
    maxFileMB: number;
    maxPassages: number;
    readsKinds: string[];
    reads: string;
    readTimeoutSeconds: number;
  };
  search: SearchFacts;
  prefs: DocumentsPrefs;
  installEnabled: boolean;
  ownerReadsChats: boolean;
}

/** Why the documents cannot be read, said by the server: a 503 with the words, a
 * code, and whether the search can be made again from the person's own files. */
export interface DocumentsBlocked {
  message: string;
  code: string | null;
  canRebuild: boolean;
}

/** `POST /api/documents/rebuild`: how many documents were made again, and how many
 * files no document could be made of. */
export interface Rebuilt {
  documents: number;
  left: number;
}

/** `POST /api/documents/meaning/rebuild`: how many passages are owed their meaning now,
 * and the header as it is after. Words are never touched. */
export interface MeaningRebuilt {
  passages: number;
  search: SearchFacts;
}

/** How a passage was found: by its words, by what it says, or by both. */
export type Via = "words" | "meaning" | "both";

/** Where each way of searching put a passage, 1 being its best, and where the result sorter
 * put it among the passages it was given (`sorter`: only the best of each way are given).
 * Never a score: a way's scores are of a scale that means nothing beside another's. */
export interface Places {
  words?: number;
  meaning?: number;
  sorter?: number;
}

/** One passage `POST /api/documents/test` found, with why it matched. `similarity` is how
 * near the question it was by meaning, on that way's own scale (null when meaning did not
 * find it). */
export interface TestedPassage {
  rank: number;
  docId: string;
  name: string;
  scope: "library" | "chat";
  pageStart: number | null;
  pageEnd: number | null;
  lineStart: number | null;
  lineEnd: number | null;
  heading: string | null;
  snippet: string;
  text: string;
  tokens: number;
  chunkIds: number[];
  via: Via;
  why: {
    via: Via;
    terms: string[];
    phrases: string[];
    inHeading: string[];
    ranks: Places;
    similarity: number | null;
  };
}

/** What a search by meaning came to, for a person to be told (`saved_api.meaning_block`):
 * whether it ran, with which search model; if not, why, in words (`quiet` when it was never
 * in play and is not worth saying beside every answer); its best closeness, the floor
 * under which that is not an answer by itself, and whether its passages were set aside. */
export interface MeaningRun {
  used: boolean;
  code: string;
  message: string | null;
  quiet: boolean;
  model: string | null;
  best: number | null;
  floor: number | null;
  belowFloor: boolean;
}

/** What sorting the passages of a search came to (`saved_api.sort_block`): whether the best
 * passages were put in a result sorter's order (`used`) and by which sorter; if not, why, in
 * the server's words (`code` and `message`; `quiet` when no sorter was ever in play), with the
 * numbers it was decided by: the tokens to read, the speed the sorter was priced at, the seconds
 * that would take, and the Sort time allowed. `seconds` is how long a sort took. Never a score. */
export interface SortRun {
  used: boolean;
  code: SortCode | null;
  message: string | null;
  quiet: boolean;
  model: string | null;
  setting: string | null;
  passages: number;
  tokens: number | null;
  ratePerSecond: number | null;
  expectedSeconds: number | null;
  budgetSeconds: number | null;
  seconds: number | null;
}

/** The ways of searching a question can ask to be tried by (`ways`). */
export type Way = "words" | "meaning";

/** `POST /api/documents/test`: the passages that would be sent for a question, with
 * no model asked. `unavailable` is only for a run that named its ways, and none of them
 * could run; `meaning` says why. `mode` is how it searched. */
export interface TestedQuestion {
  status: "found" | "nothing_matched" | "no_words" | "no_documents" | "unavailable";
  query: { text: string; terms: string[]; phrases: string[] };
  mode: "words" | "meaning" | "meaning and words" | "none" | SortedMode;
  meaning: MeaningRun;
  /** Whether the passages were sorted, by which sorter, and if not, why. */
  sort?: SortRun;
  passages: TestedPassage[];
  found: number;
  didNotFit: number;
  budget: {
    window: number | null;
    used: number;
    reserve: number;
    limit: number;
    room: number | null;
    allowed: number;
    block: number;
    perPassage: number;
  };
  searched: DocumentCounts;
}

/** Why a chat's next request sends no passages of the person's documents, in the
 * order a person can act on it. */
export type DocumentsReason =
  "install_off" | "no_model" | "model_unknown" | "window_too_small" | "outside_model" | "chat_off";

/** `GET /api/chats/{id}/documents`, for a chat that is the asker's. */
export interface ChatDocuments {
  chatId: string;
  readOnly: false;
  switch: "on" | "off";
  documentsOnly: boolean;
  /** Whether the next request searches anything: the person's documents, this
   * chat's own searched files, or both. */
  sending: boolean;
  searching: { library: boolean; chat: boolean };
  reason: string | null;
  reasonCode: DocumentsReason | null;
  hint: string | null;
  model: {
    id: string;
    window: number | null;
    locality: "local" | "external" | "unknown";
    runsOn: string | null;
  } | null;
  outside: {
    choice: ExternalChoice;
    setBy: "owner" | "you" | "default";
    canSendForThisChat: boolean;
    sendForThisChat: boolean;
  } | null;
  counts: { library: DocumentCounts; chat: DocumentCounts };
  chatDocuments: SavedDocument[];
  budget: { limit: number; window: number | null };
  search: SearchFacts;
}

/** The install's owner reading someone else's chat: whether Documents was on, and
 * nothing of the person's documents. */
export interface ChatDocumentsReadOnly {
  chatId: string;
  readOnly: true;
  switch: "on" | "off";
  documentsOnly: boolean;
}

export type ChatDocumentsView = ChatDocuments | ChatDocumentsReadOnly;

/** What searching saved documents came to for one answer: `used` (passages were
 * sent), `nothing_matched` (a normal result), `skipped` (not searched, and `code`
 * says why), `failed` (it could not run, and `message` says what was seen) or
 * `no_documents` (a chat that answers only from documents had none). */
export type SearchStatus = "used" | "nothing_matched" | "skipped" | "failed" | "no_documents";

/** `Message.retrieval`, as `saved_api.retrieval_view` makes it. */
export interface SearchRecord {
  status: SearchStatus;
  code: string | null;
  message: string | null;
  query: string | null;
  terms: string[];
  phrases: string[];
  mode: "words" | "meaning" | "meaning and words" | "none" | SortedMode;
  scope: { library: boolean; chat: boolean };
  documentsOnly: boolean;
  askedAnyway: boolean;
  answeredBy: "workbench" | null;
  passages: number;
  documents: number;
  found: number;
  didNotFit: number;
  tokens: number;
  budget: {
    window: number | null;
    used: number;
    reserve: number;
    limit: number;
    room: number | null;
    allowed: number;
  } | null;
  searched: DocumentCounts | null;
  notes: { code: string; message: string; hint?: string }[];
  /** The numbers the answer's own text cites, once it is done. */
  cited: number[];
  ms: number | null;
  /** Whether search by meaning ran for this answer, with which search model, and if not,
   * why; null for a search that was not run (MR5). */
  meaning: MeaningRun | null;
  /** Whether the passages were sorted by a result sorter, and if not, why (MR6b). Absent from
   * an answer made before sorting. */
  sort?: SortRun | null;
}

// --- folders (MR7): `/api/documents/folders`, as `folder_docs_api.py` answers ---

/** Where a folder stands: `waiting` (its turn is soon, or it was never checked), `syncing` (a
 * check is under way), `synced`, `problem` (the last check could not finish), `unavailable` (the
 * folder is not there, or another one is in its place) and `withdrawn` (the owner took the
 * grant away). */
export type FolderState =
  "waiting" | "syncing" | "synced" | "problem" | "unavailable" | "withdrawn";

/** A check under way: `looking` (the walk) and then `reading` (`done` of `total` files taken). */
export interface FolderRun {
  phase: string;
  done: number;
  total: number;
  /** Epoch seconds. */
  since: number;
  /** Whether the person asked for it (Sync now). */
  manual: boolean;
  /** A deep check reads and compares every file. */
  deep: boolean;
}

/** What a folder holds, counted by the server. */
export interface FolderCounts {
  files: number;
  byState: Record<string, number>;
  /** For the files that were skipped or failed: how many of each reason. */
  byCode: Record<string, number>;
  passages: number;
  embedded: number;
  owed: number;
  bytes: number;
}

/** The limits a folder is held to, and how near this one is. */
export interface FolderCaps {
  files: { limit: number; found: number; reached: boolean };
  bytes: { limit: number; used: number; reached: boolean };
  /** `reached` counts the folders that were too deep to be looked at. */
  depth: { limit: number; reached: number };
  entries: { reached: boolean };
  fileBytes: { limit: number };
}

/** What the last check did. */
export interface FolderLast {
  finishedAt: number | null;
  seconds: number | null;
  mode: "quick" | "now" | "deep" | null;
  added: number;
  changed: number;
  moved: number;
  removed: number;
  skipped: number;
  unchanged: number;
  files: number;
  directories: number;
  ignored: { files: number; folders: number };
  links: number;
  unlistedTotal: number;
}

/** Why a folder is not as it should be, in the server's words, and when. A withdrawn or missing
 * folder also says how many documents it took out of the search. */
export interface FolderProblem {
  code: string;
  message: string;
  at: number;
  removed?: number;
}

/** One of the person's folders (`folder_docs_api.folder_view`). A person never gets a path. */
export interface DocumentFolder {
  id: string;
  name: string;
  state: FolderState;
  headline: string;
  granted: boolean;
  writable: boolean;
  ignore: string[];
  includeHidden: boolean;
  useGitignore: boolean;
  addedAt: number | null;
  syncedAt: number | null;
  checkedAt: number | null;
  /** When the next check is due; only while `synced`. */
  nextAt: number | null;
  syncing: FolderRun | null;
  counts: FolderCounts;
  caps: FolderCaps;
  last: FolderLast | null;
  problem: FolderProblem | null;
}

/** What a check left out: `kind` is `file`, `folder`, `link` or `other`, `code` says which rule
 * or limit, and `reason` is the server's sentence. */
export interface LeftOut {
  path: string;
  kind: string;
  code: string;
  reason: string;
}

/** A folder with everything its last check left out (`GET /api/documents/folders/{id}`). */
export interface DocumentFolderDetail extends DocumentFolder {
  left: LeftOut[];
  /** Every one that was left out, by code: `left` holds the first 200 of them. */
  leftCounts: Record<string, number>;
  /** Directories that could not be looked into, and what was seen. */
  unlisted: { path: string; cause: string }[];
}

/** A folder the person may add: its name, never its path. */
export interface FolderChoice {
  id: string;
  name: string;
  writable: boolean;
  added: boolean;
}

/** The limits Workbench states, and the rules that are always in force. */
export interface FolderLimits {
  syncMinutes: number;
  maxFiles: number;
  maxMB: number;
  maxBytes: number;
  maxDepth: number;
  maxFileMB: number;
  maxFileBytes: number;
  maxPassages: number;
  maxIgnore: number;
  maxPattern: number;
  reads: string;
  rules: {
    folders: string[];
    files: string[];
    temporary: string[];
    hidden: string;
    gitignore: string;
    list: string;
  };
}

/** `GET /api/documents/folders`. `available` is false where this Workbench has no account of its
 * own to read folders with, and `reason` says so. */
export interface DocumentFolders {
  available: boolean;
  reason: string | null;
  enabled: boolean;
  folders: DocumentFolder[];
  grants: FolderChoice[];
  limits: FolderLimits;
}

/** A document read from a folder, with the folder and its path below it. */
export interface FolderFile extends SavedDocument {
  folderId: string | null;
  path: string;
}

/** `GET /api/documents/folders/{id}/files`: a page of a folder's documents in path order. */
export interface FolderFiles {
  folderId: string;
  files: FolderFile[];
  total: number;
  offset: number;
  limit: number;
  counts: FolderCounts;
}
