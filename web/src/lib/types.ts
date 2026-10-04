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
}

export interface ToolCall {
  id: string;
  serverId: string;
  serverName: string;
  tool: string;
  arguments: Record<string, unknown>;
  status: "pending" | "running" | "done" | "declined" | "cancelled" | "failed" | "uncertain";
  result: string | null;
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
  node?: string;
  available?: boolean;
  reason?: string | null;
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
  | { type: "signed-out"; reason: string; message: string };

export interface Person {
  sub: string;
  name: string;
  chats: number;
}
