/** What Workbench's own API answers with (src/eugene_plexus_workbench/api.py). */

export type MessageStatus = "done" | "running" | "stopped" | "interrupted" | "failed";

export interface Source {
  url: string;
  title: string;
}

export interface AttachedFile {
  id: string;
  name: string;
  mediaType: string;
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
  status: "pending" | "running" | "done" | "declined" | "cancelled" | "failed" | "uncertain";
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
}

/** What a rule says of a group of tools (J70). */
export type Decision = "allow" | "ask" | "deny";

export interface SiteRules {
  read: Decision;
  change: Decision;
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

export interface JobSiteFolder {
  id: string;
  name: string;
  /** Absent since 2b.3b: Eugene keeps no paths (J76). */
  path?: string | null;
  writable: boolean;
  people: { person: string; name: string; writable: boolean }[];
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
   * see only your own there. Absent from an older Eugene: `owner`. */
  role?: "owner" | "linked";
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
  folders: JobSiteFolder[];
  /** The local servers its administrator added at the machine. */
  servers?: JobSiteServer[];
  /** Whether Eugene's owner may use folders here while Eugene is in dev mode (J6e). */
  ownerInDevMode?: boolean | null;
  /** Who has linked their own OS account on the machine (no person names). */
  links?: SiteLink[];
  /** Where people link at the machine; null when they link another way. */
  linkPage?: string | null;
  /** False where only the machine's owner is served (macOS). */
  sharing?: boolean;
  /** Whether the machine checks its owner's changes with the owner's own key
   * (J14a). Absent from a machine older than that. */
  signing?: SiteSigning;
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
  /** The machine keeps each linked person's own workspaces and keys (2b.3b). */
  people?: boolean;
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
}

/** What Eugene says about whose account a person's calls run as on a machine.
 * Absent for a machine that predates linking. */
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
  stage: "prompt" | "working" | "tool";
  tool?: string;
  /** On `tool`: absent from a gateway before alpha.6, which sends only the start. */
  phase?: "started" | "finished" | "approval";
  prompt_tokens?: number;
  cached_tokens?: number;
  processed_tokens?: number;
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
