/**
 * What the folder tests share: a person's folders, their files and what a check left out, as the
 * server answers with them (src/eugene_plexus_workbench/folder_docs_api.py). The numbers are the
 * ones its own test world produces: a granted folder of notes with a code word in one Markdown
 * file, a picture that is skipped, and a few things that are left out. Read only by tests; the
 * page never imports it.
 */

import { ApiError, api, del, fileOf, patch, post } from "./lib/api";
import type {
  DocumentFolder,
  DocumentFolderDetail,
  DocumentFolders,
  FolderChoice,
  FolderCounts,
  FolderFile,
  FolderFiles,
  FolderLimits,
  LeftOut,
} from "./lib/types";
import { saved } from "./test-saved";

/** 2026-10-11 15:00 UTC, in epoch seconds: the time these folders are told against. */
export const NOW = Date.UTC(2026, 9, 11, 15, 0, 0) / 1000;

export const LIMITS: FolderLimits = {
  syncMinutes: 10,
  maxFiles: 10_000,
  maxMB: 2048,
  maxBytes: 2048 * 1024 * 1024,
  maxDepth: 12,
  maxFileMB: 50,
  maxFileBytes: 50 * 1024 * 1024,
  maxPassages: 100_000,
  maxIgnore: 100,
  maxPattern: 200,
  reads: "Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
  rules: {
    folders: [".git", "node_modules", "__pycache__", ".venv", "site-packages", "$RECYCLE.BIN"],
    files: ["Thumbs.db", "desktop.ini", ".DS_Store"],
    temporary: ["~$*", "*.tmp", "*.swp", "*~", "*.crdownload"],
    hidden: "Names that start with a dot, and files and folders Windows marks hidden.",
    gitignore: "A folder's own .gitignore files, at any depth.",
    list: "At most 100 patterns, written like .gitignore lines.",
  },
};

/** The counts of a folder whose files are all read, but for those that are skipped. */
export function counts(over: Partial<FolderCounts> = {}): FolderCounts {
  const base: FolderCounts = {
    files: 12,
    byState: {
      queued: 0,
      reading: 0,
      searchable: 0,
      embedding: 0,
      ready: 10,
      skipped: 2,
      failed: 0,
    },
    byCode: { not_document: 2 },
    passages: 31,
    embedded: 0,
    owed: 0,
    bytes: 48_200,
  };
  return { ...base, ...over };
}

/** The counts of a folder with nothing read from it: a folder not looked at yet, one that is not there,
 * and one whose documents the owner's withdrawal took out. */
export const noFiles = (): FolderCounts =>
  counts({
    files: 0,
    byState: { ...counts().byState, ready: 0, skipped: 0 },
    byCode: {},
    passages: 0,
    bytes: 0,
  });

/** A folder that is synced: checked three minutes ago, the next check in seven. */
export function folder(over: Partial<DocumentFolder> = {}): DocumentFolder {
  return {
    id: "g1",
    name: "Work notes",
    state: "synced",
    headline: "Checked 3 minutes ago, and again every 10 minutes. 12 files: 10 ready, 2 skipped.",
    granted: true,
    writable: false,
    ignore: [],
    includeHidden: false,
    useGitignore: true,
    addedAt: NOW - 86_400,
    syncedAt: NOW - 180,
    checkedAt: NOW - 180,
    nextAt: NOW + 420,
    syncing: null,
    counts: counts(),
    caps: {
      files: { limit: 10_000, found: 12, reached: false },
      bytes: { limit: 2048 * 1024 * 1024, used: 48_200, reached: false },
      depth: { limit: 12, reached: 0 },
      entries: { reached: false },
      fileBytes: { limit: 50 * 1024 * 1024 },
    },
    last: {
      finishedAt: NOW - 180,
      seconds: 0.4,
      mode: "quick",
      added: 0,
      changed: 0,
      moved: 0,
      removed: 0,
      skipped: 0,
      unchanged: 12,
      files: 12,
      directories: 3,
      ignored: { files: 0, folders: 0 },
      links: 0,
      unlistedTotal: 0,
    },
    problem: null,
    ...over,
  };
}

/** A folder never checked: waiting for its first look. */
export const waitingFolder = (over: Partial<DocumentFolder> = {}) =>
  folder({
    state: "waiting",
    headline: "Looking at this folder soon, for the first time.",
    syncedAt: null,
    checkedAt: null,
    nextAt: null,
    counts: noFiles(),
    last: null,
    ...over,
  });

/** A check under way: still looking, or reading `done` of `total`. */
export const syncingFolder = (
  run: Partial<NonNullable<DocumentFolder["syncing"]>> = {},
  over: Partial<DocumentFolder> = {},
) =>
  folder({
    state: "syncing",
    nextAt: null,
    syncing: {
      phase: "reading",
      done: 1_200,
      total: 4_000,
      since: NOW - 20,
      manual: false,
      deep: false,
      ...run,
    },
    ...over,
  });

/** Files read, and a check that found 15 of 404 still being read. */
export const readingFolder = (over: Partial<DocumentFolder> = {}) =>
  folder({
    counts: counts({
      files: 404,
      byState: {
        queued: 9,
        reading: 2,
        searchable: 3,
        embedding: 1,
        ready: 380,
        skipped: 9,
        failed: 0,
      },
      byCode: { not_document: 9 },
      passages: 1_904,
      bytes: 4_400_000,
    }),
    ...over,
  });

/** The last check could not finish. */
export const problemFolder = (over: Partial<DocumentFolder> = {}) =>
  folder({
    state: "problem",
    nextAt: null,
    problem: {
      code: "unreadable",
      message:
        "Workbench could not look at this folder (permission denied). Its files are as they were at the last check.",
      at: NOW - 60,
    },
    ...over,
  });

/** The folder is not there. */
export const goneFolder = (over: Partial<DocumentFolder> = {}) =>
  folder({
    state: "unavailable",
    nextAt: null,
    counts: noFiles(),
    problem: {
      code: "gone",
      message:
        "This folder is not there any more. 12 documents from it were taken out of your search, and they come back if the folder does.",
      at: NOW - 600,
      removed: 12,
    },
    ...over,
  });

/** The owner took the grant away. */
export const withdrawnFolder = (over: Partial<DocumentFolder> = {}) =>
  folder({
    state: "withdrawn",
    nextAt: null,
    granted: false,
    counts: noFiles(),
    problem: {
      code: "withdrawn",
      message:
        "The owner took away your access to this folder. 12 documents from it were taken out of your search.",
      at: NOW - 2 * 86_400,
      removed: 12,
    },
    ...over,
  });

export const choice = (over: Partial<FolderChoice> = {}): FolderChoice => ({
  id: "g2",
  name: "Field reports",
  writable: false,
  added: false,
  ...over,
});

/** `GET /api/documents/folders`. */
export function foldersPage(
  folders: DocumentFolder[] = [],
  over: Partial<Omit<DocumentFolders, "folders">> = {},
): DocumentFolders {
  return {
    available: true,
    reason: null,
    enabled: true,
    folders,
    grants: folders.map((f) => choice({ id: f.id, name: f.name, added: true })),
    limits: LIMITS,
    ...over,
  };
}

/** One of a folder's files, ready: a Markdown file at a path below the folder. */
export function folderFile(over: Partial<FolderFile> = {}): FolderFile {
  return {
    ...saved({
      id: "f1",
      name: "Work notes/docs/harbour-notes.md",
      mediaType: "text/markdown",
      kind: "markdown",
      label: "Markdown",
      pages: null,
      lines: 24,
      size: 1_800,
      passages: 3,
    }),
    folderId: "g1",
    path: "docs/harbour-notes.md",
    ...over,
  };
}

/** A picture in the folder: skipped, with the server's own reason. */
export const pictureFile = (over: Partial<FolderFile> = {}) =>
  folderFile({
    id: "f2",
    name: "Work notes/picture.png",
    path: "picture.png",
    mediaType: "image/png",
    kind: "image",
    label: null,
    lines: null,
    size: 2_048,
    passages: 0,
    tokens: null,
    state: "skipped",
    searchable: false,
    code: "not_document",
    message:
      "Skipped: this file is a picture, not a document. Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
    ...over,
  });

/** A file whose reading ended badly. */
export const brokenFile = (over: Partial<FolderFile> = {}) =>
  folderFile({
    id: "f3",
    name: "Work notes/broken.pdf",
    path: "broken.pdf",
    mediaType: "application/pdf",
    kind: "pdf",
    label: "PDF",
    state: "failed",
    searchable: false,
    passages: 0,
    code: "damaged",
    message: "Failed: Workbench could not read this file (the PDF is cut off).",
    ...over,
  });

/** A file waiting to be read. */
export const waitingFile = (over: Partial<FolderFile> = {}) =>
  folderFile({
    id: "f4",
    name: "Work notes/later.md",
    path: "later.md",
    state: "queued",
    searchable: false,
    passages: 0,
    ...over,
  });

/** `GET /api/documents/folders/{id}/files`: these files, as the page they fill. */
export function filesPage(
  files: FolderFile[],
  over: Partial<FolderFiles> = {},
  of: DocumentFolder = folder(),
): FolderFiles {
  return {
    folderId: of.id,
    files,
    total: files.length,
    offset: 0,
    limit: 50,
    counts: of.counts,
    ...over,
  };
}

/** One thing a check left out. */
export const leftOut = (over: Partial<LeftOut> = {}): LeftOut => ({
  path: "node_modules",
  kind: "folder",
  code: "ignored_default",
  reason: "Ignored: a folder that holds other programs' files (node_modules).",
  ...over,
});

/** `GET /api/documents/folders/{id}`: the folder, and what it left out. */
export function detail(
  over: Partial<DocumentFolderDetail> = {},
  of: DocumentFolder = folder(),
): DocumentFolderDetail {
  return {
    ...of,
    left: [
      leftOut(),
      leftOut({
        path: ".env",
        kind: "file",
        code: "ignored_hidden",
        reason: "Ignored: a name that starts with a dot (hidden).",
      }),
      leftOut({
        path: "build/out.js",
        kind: "file",
        code: "ignored_gitignore",
        reason: "Ignored: a .gitignore file lists it (build/).",
      }),
      leftOut({
        path: "shortcut",
        kind: "link",
        code: "link",
        reason: "Not followed: this is a link. Workbench never follows links out of the folder.",
      }),
    ],
    leftCounts: { ignored_default: 1, ignored_hidden: 1, ignored_gitignore: 1, link: 1 },
    unlisted: [],
    ...over,
  };
}

// --- a Workbench that keeps folders, as far as the page can tell ---

/** The refusal the server makes: its sentence, in `detail.message`. */
export const refusal = (status: number, message: string, extra: object = {}) =>
  new ApiError(message, status, { message, ...extra });

/** A fake Workbench for the Folders section: it keeps a list of folders, their files and what a
 * check left out, answers each route the page uses as `folder_docs_api.py` does, and keeps what it
 * was asked in `calls`. A test sets `refuse` for a call that must fail, by `METHOD path`. The
 * page's calls (`api`, `post`, `patch`, `del`, `fileOf`) must be mocked by the test file. */
export class FakeFolders {
  page: DocumentFolders;
  details = new Map<string, DocumentFolderDetail>();
  files = new Map<string, FolderFile[]>();
  calls: { method: string; path: string; body?: unknown }[] = [];
  refuse = new Map<string, Error>();
  /** The next id a folder that is added is given. */
  next = 10;

  constructor(
    folders: DocumentFolder[] = [],
    over: Partial<Omit<DocumentFolders, "folders">> = {},
  ) {
    this.page = foldersPage(folders, over);
  }

  /** The calls of one method, as `METHOD path` with the body. */
  made(method: string): { path: string; body?: unknown }[] {
    return this.calls.filter((c) => c.method === method).map(({ path, body }) => ({ path, body }));
  }

  folder(id: string): DocumentFolder {
    const found = this.page.folders.find((f) => f.id === id);
    if (!found) throw new Error(`no folder ${id}`);
    return found;
  }

  /** Change a folder, as a check would. */
  update(id: string, over: Partial<DocumentFolder>): void {
    this.page.folders = this.page.folders.map((f) => (f.id === id ? { ...f, ...over } : f));
  }

  private note(method: string, path: string, body?: unknown): void {
    this.calls.push({ method, path, body });
    const key = `${method} ${path.split("?")[0]}`;
    const failure = this.refuse.get(key);
    if (failure) throw failure;
  }

  install(): void {
    vi.mocked(api).mockImplementation((async (path: string) => {
      this.note("GET", path);
      const url = new URL(path, "http://workbench");
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length === 3) return structuredClone(this.page);
      const id = decodeURIComponent(parts[3] ?? "");
      if (parts.length === 4) {
        const held = this.details.get(id);
        return structuredClone(held ?? detail({}, this.folder(id)));
      }
      if (parts[4] === "files") return structuredClone(this.list(id, url.searchParams));
      throw new Error(`not served: ${path}`);
    }) as never);
    vi.mocked(post).mockImplementation((async (path: string, body?: Record<string, unknown>) => {
      this.note("POST", path, body);
      const parts = path.split("/").filter(Boolean);
      if (parts.length === 3) return this.add(body ?? {});
      if (parts[4] === "sync") {
        const id = decodeURIComponent(parts[3] ?? "");
        this.update(id, { state: "waiting", nextAt: null });
        return this.folder(id);
      }
      throw new Error(`not served: ${path}`);
    }) as never);
    vi.mocked(patch).mockImplementation((async (path: string, body: Record<string, unknown>) => {
      this.note("PATCH", path, body);
      const id = decodeURIComponent(path.split("/").filter(Boolean)[3] ?? "");
      this.update(id, {
        ignore: (body.ignore as string[] | undefined) ?? this.folder(id).ignore,
        includeHidden: (body.includeHidden as boolean | undefined) ?? this.folder(id).includeHidden,
        useGitignore: (body.useGitignore as boolean | undefined) ?? this.folder(id).useGitignore,
        state: "waiting",
        nextAt: null,
      });
      return this.folder(id);
    }) as never);
    vi.mocked(del).mockImplementation((async (path: string) => {
      this.note("DELETE", path);
      const parts = path.split("/").filter(Boolean);
      if (parts[2] === "folders") {
        const id = decodeURIComponent(parts[3] ?? "");
        this.page.folders = this.page.folders.filter((f) => f.id !== id);
        this.page.grants = this.page.grants.map((g) => (g.id === id ? { ...g, added: false } : g));
        return undefined;
      }
      // A file of a folder: it leaves, and its path goes on the folder's ignore list.
      const doc = decodeURIComponent(parts[2] ?? "");
      for (const [id, list] of this.files) {
        const gone = list.find((f) => f.id === doc);
        if (!gone) continue;
        this.files.set(
          id,
          list.filter((f) => f.id !== doc),
        );
        this.update(id, { ignore: [...this.folder(id).ignore, `/${gone.path}`] });
      }
      return undefined;
    }) as never);
    vi.mocked(fileOf).mockImplementation((async (path: string) => {
      this.note("GET", path);
      return { blob: new Blob(["copy"]), name: "harbour-notes.md" };
    }) as never);
  }

  private add(body: Record<string, unknown>): DocumentFolder {
    const grant = this.page.grants.find((g) => g.id === body.grantId);
    if (!grant) throw refusal(404, "There is no such folder.");
    if (grant.added) throw refusal(409, "This folder is already in your documents.");
    const made = waitingFolder({
      id: grant.id,
      name: grant.name,
      ignore: (body.ignore as string[] | undefined) ?? [],
      includeHidden: (body.includeHidden as boolean | undefined) ?? false,
      useGitignore: (body.useGitignore as boolean | undefined) ?? true,
    });
    this.page.folders = [...this.page.folders, made];
    this.page.grants = this.page.grants.map((g) => (g.id === grant.id ? { ...g, added: true } : g));
    this.next += 1;
    return made;
  }

  /** The page of files the server would give for these filters, in path order. */
  private list(id: string, query: URLSearchParams): FolderFiles {
    const state = query.get("state");
    const code = query.get("code");
    const word = (query.get("q") ?? "").toLowerCase();
    const offset = Number(query.get("offset") ?? 0);
    const limit = Number(query.get("limit") ?? 200);
    const all = (this.files.get(id) ?? [])
      .filter((f) => (state ? f.state === state : true))
      .filter((f) => (code ? f.code === code : true))
      .filter((f) => f.path.toLowerCase().includes(word))
      .sort((a, b) => a.path.localeCompare(b.path));
    const of = this.folder(id);
    return {
      folderId: id,
      files: all.slice(offset, offset + limit),
      total: all.length,
      offset,
      limit,
      counts: of.counts,
    };
  }
}
