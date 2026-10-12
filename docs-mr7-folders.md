# MR7: folders (designed and built 2026-10-11)

A person points their Documents at a folder they have been granted. Workbench reads its files, read-only,
into the person's own index and keeps them in step: a new file is added, an edited file is read again and its
old words are gone in the same transaction, a deleted file is removed, and a folder that goes away or a
grant that is withdrawn takes its documents out of the search, said in words. A chat with Documents on
searches them like any other document, by words and by meaning, and the Sources name the file by its path.

The pieces already built are used as they are: the per-person index (`knowledge/index.py`), the reader
(a child process, 120 s, 2 GiB), the ingest worker, words and meaning search. MR7 adds a way in (a walk of a
granted folder), a sync that decides what changed, and the routes the page needs. Nothing in `web/`.

## What I read first

- A grant (`folder_grants`: id, name, path, identity, subject, writable) is made by the **owner only**, for one
  signed-in person (`subject`). That person can then switch it on in a chat, where a model lists and reads
  one file at a time (32 KiB) and every call is approved. `Folders` serialises every file call and grant change
  under one lock, so once removal returns no earlier call can open a file under that grant. Folder tools only
  work when Workbench runs as its own OS account (`account_kind` is `windows_service` or `systemd`).
- Every file call goes through held handles, never a checked-then-opened path: Windows opens one name at a
  time relative to a retained parent handle with `FILE_OPEN_REPARSE_POINT`, refuses any reparse point, and keeps
  parents from being renamed; Linux opens with `O_NOFOLLOW` one component at a time, refuses mount points and
  runs inside a fresh thread under Landlock (read-only beneath the folder). Only ordinary files with one hard
  link are read. The folder's identity (device, inode, birth time) is pinned at grant time.
- Folders on **other machines** (Job Sites, `node:<id>`) are reached through Eugene with the person's own
  sign-in on every call. A background sync has no sign-in to use.

## The calls, with reasons

1. **Which folders.** Only a grant that names this person (`subject`), on this machine. The owner adds only
   grants that name the owner; granting Ada a folder does not let the owner index it (no role sees everything),
   and Ada adds it herself. A writable grant is fine, because indexing only reads. **Job Sites' folders are
   refused with a plain reason** (MRC26): not trivial, since the sync would need the person's session, and it
   would put a remote machine's files in a central index. A person never sees the host path (only the owner does,
   in `GET /api/folders`); documents are named by the grant's name and a path inside it.
2. **Where the files go.** Workbench **copies** each readable file into the person's own `originals/` (the
   same place an upload goes) and reads that copy, so extraction never touches the granted path, a Rebuild
   works from the person's own files, and "Show indexed text" and "Open original" work. The copy is the
   cost: disk use is bounded by the caps below. A document's `source` is `folder:<grant id>/<relative path>`
   (Appendix B) and its `name` is `<grant name>/<relative path>`, so two folders can both hold a `README.md`
   and a Source says which; a question that names `README.md` still finds it (the match is on the file's own
   name). It stays in scope `library`, so every chat with Documents on searches it with no change to
   search. A file of the same name no longer replaces a folder file (the same *path* does), and an upload
   never replaces one.
3. **How a folder is synced: a rescan, not a watcher.** Every `documentsFolderSyncMinutes` (10; 1 to 1,440)
   Workbench lists the folder and compares each file's size and modified time with what the index holds; a
   file that differs is read and **hashed (SHA-256), and the hash decides**: a touched or re-saved file with the
   same content costs one read and is not read again, and only a real change makes a new document. A file
   modified within 2.5 s of being read is kept with a `!` after its time and read again at the next check (a
   file system with a coarse clock can miss a second edit inside one tick). **Sync now** checks at once, before
   any scheduled check. A file skipped for a reason that may pass by itself (another program is using it, it
   changed while being read, the system said no) is tried again at *every* check; a file over a limit the owner
   can raise (the file size, the folder's size, the passages) and any file that failed are tried again by Sync
   now; **a deep check** (`deep`) reads and hashes every file. **Every folder is also checked once when
   Workbench starts**, so a check a stop cut off is finished and what changed while it was stopped is found.
   A renamed or moved file is recognised by its size, modified time and hash and **moves its document with its
   passages and vectors**, so renaming a folder does not pay for embedding it all again. No file-system
   watcher: it would hold a handle on every directory (which blocks renames) or one inotify watch per
   directory (a limit people hit), misses events silently on overflow and sleep, and the walk of 10,000
   files takes about a tenth of a second (measured: 0.07 s on this PC). The costs of a rescan are that a stale
   answer can last until the next check, which the screen says ("Checked 3 minutes ago, and again every 10
   minutes."), and that **an edit that keeps a file's size and its modified time is missed by a quick check**,
   as in every tool that trusts both; a deep check finds it.
4. **Ignore rules.** Always ignored, even with hidden files turned on: `.git`, `.hg`, `.svn`, `node_modules`,
   `__pycache__`, `.venv`, `venv`, `site-packages`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`,
   `.cache`, `.idea`, `.vscode`, `$RECYCLE.BIN`, `System Volume Information`, `.Trash`, `.Trashes` (folders),
   and the files `Thumbs.db`, `desktop.ini`, `.DS_Store`. Temporary files (files, not folders): `~$*`,
   `.~lock.*#`, `*.tmp`, `*.temp`, `*.swp`, `*.swo`, `*~`, `.#*`, `*.part`, `*.partial`, `*.crdownload`.
   Hidden files and folders (a name that starts with a dot, or the Windows hidden or system attribute) are
   ignored unless the person turns **Include hidden** on for that folder. A folder's own `.gitignore` files
   (at any depth, each for its own subtree, with `!`) are respected unless the person turns that off,
   because a repository's build output is not what they mean to search. The **per-folder ignore list** uses
   the same pattern language (`*`, `**`, `?`, `[ab]`, a leading `/` to anchor, a trailing `/` for folders,
   `\` to escape), at most 100 patterns of at most 200 characters, with no `!` (it only ignores) and no `#`.
   An ignored folder is not entered. Ignored things are named in the folder's report (path and rule, up to
   200 and the total), not made into documents. Names are matched without regard to case on Windows.
5. **Caps, stated on screen** (settings in the config trio): files per folder **10,000**
   (`documentsFolderMaxFiles`), bytes copied per folder **2,048 MB** (`documentsFolderMaxMB`), depth **12**
   folders below the one granted (`documentsFolderMaxDepth`), and the existing per-file limit
   (`documentsMaxFileMB`, 50) and passages per person (`documentsMaxPassagesPerPerson`, 100,000). A walk is
   in name order, shallow files first, so what is left out when a cap is reached is the same every time
   and is said ("Left out: the folder has more than the 10,000 files Workbench keeps for one folder."). The
   bytes cap counts the copies Workbench keeps, not the pictures and programs it skips, and counts what the
   folder already holds at every check, not only what it reads in that one. Workbench also stops looking
   after 200,000 names, and says so.
6. **Per-file reasons.** Every file that gets past the ignore rules is a document row; one that cannot be
   read is `skipped` or `failed` with its reason and a code, in the words of the other screens: a type
   Workbench does not read ("This is a GIF image. Workbench reads PDF, Word, ..."), too large, over the
   folder's size, another program is using it (tried again at the next check), no permission, changed while
   being read (tried again), more than one hard link, a name Workbench cannot open, a scanned PDF, over the
   passage limit. What is not made into a document (ignored, a link, deeper than the cap, a directory that
   could not be listed) is in the folder's report with its reason. The page can list a folder's files by
   state or code.
7. **Edit, delete.** An edited file is read again as a new document and the old document, passages,
   vectors and full-text entries go **in the same transaction** (the path is the identity), so the old text is
   never found again; for the seconds it is read it is `queued` and not searchable, which is the honest
   state. A deleted file is removed with the same transaction. A file whose directory could not be listed
   (not "not found": a permission, a busy disk) is **left as it is** and named; only a file Workbench
   knows is gone is removed, so a hiccup never empties an index. Documents are removed in one transaction
   with their files: the files are first **renamed into `trash/`** beside the index, put back if anything
   fails, and deleted once the rows are gone, so a file another program holds (Windows) leaves everything as
   it was and says so, never a document with its words gone and its original left or the other way round.
8. **The folder goes, the grant goes.** A grant withdrawn by the owner takes the folder's documents out of
   the index before the owner's request returns, ends anything reading them, and leaves the folder in the
   person's list as "Withdrawn", with the count and the time, until they dismiss it. The documents go
   first and then the folder says so: a file that cannot be removed leaves the folder as it was, to be
   withdrawn again at the next look. A withdrawal is done once; whoever asks while it is being done (the
   owner's request, or a check that found its grant gone) waits for it. A folder that is **not there** (not
   found, or replaced by another folder: the pinned identity no longer matches) is the same, but the entry
   stays and is checked at each sync, and its documents come back with it. Any other failure (permission,
   busy, an unsupported host) keeps the documents and says the cause and the time of the last good check.
9. **Links.** Never followed. A symlink or a junction is not entered and not read (Windows: any reparse
   point, so also an online-only OneDrive or Dropbox file, named as such because reading it would download
   it; a file kept on the device is an ordinary file and is read). Linux mount points inside the folder are
   not entered. Hard links, devices and pipes are skipped with their reason. Each open is made through a
   held handle, so a link swapped in after the listing is refused when it is opened.
10. **Read-only.** Handles are opened for reading (Windows access mask with no write bit; `O_RDONLY` under
    Landlock on Linux), nothing is created in the folder, and the share mode lets other programs write,
    rename and delete the file while it is read, so an editor saving is never blocked. A test checks the
    folder's bytes, times and listing are the same after every kind of sync.
11. **Per person.** The folder list is in the main store, filtered by owner in every query; the documents
    are in the person's own index file. Another person's folder is the same 404 as one that does not exist.
    Saved documents stay outside `ownerReadsChats`.
12. **Removing one file** from a folder in the list adds its path to that folder's ignore list (otherwise it
    would come back at the next check), and says so. **Removing the folder** removes its documents and never
    touches the folder or the grant.

## What the page reads

`GET /api/documents` lists only what the person added themselves (`source = upload`) with matching totals;
a folder's files are under the folder (`GET /api/documents/folders/{id}/files`), so a 10,000-file folder does
not travel on every poll. Its shape is as it was. Search, the chat chip and Test a question see both. Every
route takes the person from the session and answers a folder that is not theirs with
`404 {"detail": {"message": "There is no such folder."}}`, as for one that does not exist.

| Route | |
|---|---|
| `GET /api/documents/folders` | `{available, reason, enabled, folders[], grants[{id, name, writable, added}], limits}`. `grants` are the granted folders the person may add (never a path); `limits` is `{syncMinutes, maxFiles, maxMB, maxBytes, maxDepth, maxFileMB, maxFileBytes, maxPassages, maxIgnore, maxPattern, reads, rules{folders, files, temporary, hidden, gitignore, list}}` |
| `POST /api/documents/folders` | `{grantId, ignore?, includeHidden?, useGitignore?}` adds a granted folder: 201 with the folder; its first check starts at once. 400 for a `node:` id, 403 when the owner has Documents off, 404 for a grant that is not theirs, 409 when it is added already, 422 for a pattern that cannot be used (`message` says which), 503 `{message, code: "folders_unavailable"}` when this Workbench has no account of its own |
| `GET /api/documents/folders/{id}` | the folder, plus `left[]` (up to 200 things left out: `{path, kind, code, reason}`), `leftCounts{}` (every one, by code) and `unlisted[]` (directories that could not be looked into: `{path, cause}`) |
| `PATCH /api/documents/folders/{id}` | `{ignore?, includeHidden?, useGitignore?}` (a list replaces the list); checks again at once; 409 for a folder the owner withdrew |
| `POST /api/documents/folders/{id}/sync` | `{deep?}` (optional body) Sync now: 202 with the folder; 409 for a withdrawn folder |
| `GET /api/documents/folders/{id}/files` | `?state=&code=&q=&offset=&limit=` (limit 1 to 500, default 200; 422 for a state that is not one) gives `{folderId, files[], total, offset, limit, counts{files, byState, byCode, passages, embedded, owed, bytes}}`, in path order; each file is a document as `GET /api/documents` gives it (`state`, `message` and `code` for one that was skipped or failed, `meaning`, ...) plus `folderId` and `path` |
| `DELETE /api/documents/folders/{id}` | 204; its documents leave the index (also dismisses "Withdrawn"); 500 with the cause if a file could not be removed, and nothing was |

The folder is `{id, name, state, headline, granted, writable, ignore, includeHidden, useGitignore, addedAt,
syncedAt, checkedAt, nextAt, syncing, counts, caps, last, problem}`:

- `state` is `waiting | syncing | synced | problem | unavailable | withdrawn`, and `headline` one sentence for it
  made of what is true ("Checked 3 minutes ago, and again every 10 minutes. 404 files: 380 ready, 15 being
  read, 9 skipped."). `nextAt` is set only while `synced`.
- `syncing` is `{phase, done, total, since, manual, deep}` while a check is under way, else null.
- `counts` is `{files, byState, byCode, passages, embedded, owed, bytes}`.
- `caps` is `{files{limit, found, reached}, bytes{limit, used, reached}, depth{limit, reached}, entries{reached},
  fileBytes{limit}}`: the limits, and how near this folder is.
- `last` is what the last check did: `{finishedAt, seconds, mode (quick|now|deep), added, changed, moved, removed,
  skipped, unchanged, files, directories, ignored{files, folders}, links, unlistedTotal}`; null before one ends.
- `problem` is `{code, message, at, removed?}` or null. Codes: `gone`, `replaced`, `withdrawn`, `unreadable`,
  `disk_full`, `delete_failed`, `internal`, and the index's own (a damaged or unreachable index).
- A skipped or failed file carries one of: `unsupported`, `not_document`, `bad_zip`, `too_large`,
  `over_folder_size`, `busy`, `changed`, `unreadable`, `hard_links`, `special`, `link`, `mount`, and the
  reasons the reader gives (a scanned PDF, a password, too many passages ...). What was left out of the
  listing carries one of: `ignored_default`, `ignored_hidden`, `ignored_temporary`, `ignored_list`,
  `ignored_gitignore`, `link`, `cloud`, `special`, `odd_name`, `too_deep`, `over_files`.

Routes that were there change in these ways: Remove (`DELETE /api/documents/{id}`) on a file of a folder adds
`/<path>` to the folder's ignore list and then removes it (409 when the list is full, so that it would come
back); Re-read (`POST /api/documents/{id}/reread`) on a file that was never copied is a 409 that says to use
Sync now; Open original downloads under the file's own name. Settings, in the console, category Documents:
`documentsFolderSyncMinutes` (10; 1 to 1,440), `documentsFolderMaxFiles` (10,000; 10 to 100,000),
`documentsFolderMaxMB` (2,048; 1 to 102,400), `documentsFolderMaxDepth` (12; 1 to 31).

## Checks

Unit: the rules (every default, hidden, temporary, the pattern language, `.gitignore` scoping and `!`), the walk
(caps, depth, order, links, listing failures), the plan (what is new, changed, moved, gone, kept), the index
(path identity, names, delete by source, counts, the trash), the handles (a junction or symlink, a hard link, a
file that grows, a file replaced after the listing, another program saving while it is read), the store, and
the routes with two people. Acceptance (`scripts/mr7-folders-acceptance.py`): a real Workbench, gateway,
driver and llama.cpp with Qwen3-0.6B: grant, add, ready, a question answered from one file with the Source
naming it, an edit (new text answers, old is gone from every file of the person's), a delete, an ignored file
and a picture with their reasons, a junction not followed, a larger folder read across a kill and a restart
(nothing read twice), two people against every route, the folder byte-for-byte as it was, the grant
withdrawn. Sabotage (`scripts/check-folders-sabotage.py`): each of the checks above is broken once and must
fail.

## What building found

- **A rule changed while a check was deciding was lost.** The loop looked at the folder's record, then the
  person turned *Include hidden* on, and the check used the record it had been handed (two tests failed now and
  then). A check reads the record again when it begins; a test holds the race on purpose.
- **Removing many documents was not all or nothing for their files.** Files were unlinked inside the
  transaction, so a failure on the fortieth left thirty-nine files gone and the rows back. Removal is staged
  through `trash/` (call 7), and the boot removes what a stop left there.
- **A removal that fails said "internal".** A file another program holds is `delete_failed` with the cause.
- **The order of a removal.** The record went before the documents, so a failure left a folder that was not
  listed and documents that were searchable. The documents go first.
- **A manual check kept in memory was lost by a stop.** Every folder is checked once at start (call 3).
- **A withdrawal could return early.** When a check found its grant gone at the same moment as the owner's
  request, the request saw the withdrawal "already under way" and returned before the documents were out. A
  withdrawal is a lock: the second asker waits, and a test makes the check come first.
- **A pattern that ends in a space lost it when the list was saved.** `exact()` writes `name\ ` for a file whose
  name ends in a space (Linux), and the save trimmed the list. A trailing space that a backslash makes part of
  the name is kept.
- **`GET /api/documents` and a document's shape are declared contracts** with tests, so MR7 left both as they
  were (uploads only in the list, no `source`) and put a folder file's `folderId` and `path` in the folder's own
  file list.
- **`too_large` is retried by Sync now.** The owner can raise the largest-file setting; the file was skipped
  for a limit, not for what it is.
- **Reading a file never blocks another program's save** (share read, write and delete on Windows; no lock on
  Linux), and a file open for write elsewhere is still read. Checked with real files on both systems.
- **Linux was run for real** (Ubuntu under WSL, Landlock): a thread the read starts inherits its sandbox, so a
  test's "other program" is started outside the read.
- **OneDrive.** A file that is on this PC reads like any other (it has no reparse point); one that is only
  online has the offline or recall attributes or a cloud reparse tag and is `cloud`, not downloaded.

Not built: Job Sites' folders (call 1); a file-system watcher (call 3); a per-folder interval. A first read of
many files is one reader process for each file, two at a time, as for uploads: measured here at 7 to 8 small
Markdown files a second (300 files in 41 s, the walk and the copies of them 3 s of that), so a folder of
10,000 takes about twenty minutes and is searchable by degrees while it does: the check copies the files
first (fast), and the headline says how far the reading has got ("10,000 files: 4,120 ready, 5,880 being
read"). A long PDF takes longer; the figure is for this PC.
