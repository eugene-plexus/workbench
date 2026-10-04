# Workbench

Eugene Plexus's own chat app. It lets you chat with the models your Eugene install serves, and search the web while you do. It signs people in with Eugene. One person can use it with Eugene's passphrase, or a small business can use it with an account for each person.

Workbench is an *app* (a spoke), not part of the hub. It reaches Eugene the same way any other app does:

- one client key, used at the gateway's public OpenAI-compatible endpoints;
- sign-in with Eugene over OpenID Connect.

It holds no operator or node-enrollment credential. Node folder tools additionally use Workbench's sign-in registration and the initiating person's session, checked by Eugene for each operation. The core design is [`specs/docs/design/workbench-v1.md`](https://github.com/eugene-plexus/specs/blob/main/docs/design/workbench-v1.md); [node file helpers](https://github.com/eugene-plexus/specs/blob/main/docs/design/node-file-helpers.md) describe central hosting and per-person access.

## Installing

Install Workbench from **Apps** in Eugene's console. The install does three things:

- it makes Workbench's key;
- it registers Workbench to sign people in with Eugene;
- it uses a separate OS account where the node supports one. The core can also run on Docker, macOS and per-user installations. Its own local file and subprocess tools still require the isolated Windows/Linux service account; remote node folders run in the helper's account on their machine.

Open it from the same page.

For a central Workbench, enroll each file-serving machine as an ordinary Eugene node. In **People → Files on your machines**, enable file support, register existing folders, and assign each person read or text-write access. The node prepares its bundled helper automatically; no model or separate Workbench installation is needed there. Windows service and Linux system installations currently support file helpers. Select these folders per chat in Workbench; the browser needs no route to the desktop. See the [setup and platform guide](https://github.com/eugene-plexus/specs/blob/main/docs/design/node-file-helpers.md#using-it).

Workbench's one setting is on its page in the console. It controls whether the owner may read the chats of the people the owner gives Workbench to. It is off by default.

## What it does

- **Chats**: answers stream as they arrive. You can Stop an answer, Try again, or edit a message.
- **Finding chats**: search chat names and browse Today, Yesterday, Previous 7 days and Older groups.
- **Text drafts**: unsent text survives chat switches and refreshes in the same tab. Drafts belong to the signed-in person and clear on send, deletion or sign-out. Attachments must be selected again after leaving a chat.
- **Writing**: the message box grows with your text. Drop files onto it or paste an image; upload progress says what is arriving and Send waits until it is ready.
- **Reading and saving**: Jump to latest returns to the newest answer, messages show their local time (hover for the date), and Copy confirms success or explains a blocked clipboard. Export chat saves a Markdown snapshot with text, reasoning, tool records, sources and attachment names; file contents are not included.
- **Keyboard shortcuts**: Ctrl+Alt+N starts a chat, Ctrl+Alt+F searches chat names, and Ctrl+Alt+M focuses the message box. On Mac, use Control+Option. The shortcuts are also listed in the page.
- **Markdown and code**: the model's reasoning is shown folded away.
- **Attachments**: images (PNG or JPEG), PDFs and audio (WAV or MP3). Each goes to a model that takes that kind of file.
- **Tools: Search the web**: this runs on the install's search account. When a search cannot run, the switch says why.
- **MCP tools**: the owner adds shared Streamable HTTP servers in **Toolbox · Tools**. Choose servers in a chat's settings, then approve or decline each proposed call. Credentials stay on the server. Pending approvals expire after 30 minutes; interrupted calls are never automatically repeated.
- **Folder tools**: use node folders assigned in Eugene's People page, or existing Workbench-host grants managed in **Toolbox · Tools**. Read-only is the default; text creation and editing are optional. Select folders per chat and approve each listing, read or write. File contents go to the selected model and remain in the chat.
- **Answers keep going with no tab open**: an answer is saved as it arrives. Close the tab and come back, and it is there.

Network MCP connections use HTTPS, except for loopback HTTP, with an optional bearer credential. They are shared with everyone signed into this Workbench. Tool results and arguments stay with the answer. Stopping or editing a chat does not undo actions already taken.

**Local MCP servers** are available to the install owner when the launcher confirms Workbench's own OS account. In Toolbox, save an absolute executable path, a JSON argument array and optional secret environment values. **Start and check** runs it once to discover tools. Selecting it for a chat starts a process for that answer, with approval for each call. At most four local processes can run at once; Stop and graceful shutdown close them. Failed calls are never automatically replayed.

Install the program and its dependencies separately, where Workbench's account can execute them; Workbench does not run a package installer or shell command string. On Windows use an `.exe` such as `python.exe` or `node.exe`, putting the script path in Arguments. Keep secrets in environment values, since process lists can show arguments. Stderr is discarded to keep third-party credential output out of shared logs.

Local servers are trusted code in Workbench's account: they can read its files, including people's chats, app credentials and granted folders. Their separate working folders are **not sandboxes**. Other people cannot list or use them. Per-person process isolation, OAuth sign-in to MCP servers, and media screens remain later slices; see the [MCP design](https://github.com/eugene-plexus/specs/blob/main/docs/design/workbench-mcp.md).

**Folder grants** apply to Workbench's built-in file tools. The machine administrator must first give Workbench's service account access to the specific host folder; saving a grant does not change OS permissions. Windows uses the app's service identity; Linux needs a stable group and systemd configuration. Follow the [folder provisioning guide](https://github.com/eugene-plexus/specs/blob/main/docs/design/workbench-files.md#provisioning-an-existing-folder).

Only the named recipient can use a grant. Removing it blocks pending calls but keeps files and prior chat results. File tools refuse links, special files, replaced folders and Workbench's private storage. Reads accept small UTF-8 text files; edits require the hash from a prior read and refuse changed content. Writes are in place: an interrupted or failed write may leave partial changes and is never automatically repeated. There are no delete, rename or execution operations.

## Developing

The server is Python (FastAPI). The page is React and TypeScript, built by Vite.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"
.venv/bin/pytest

cd web
npm ci
npm test
npm run build      # writes src/eugene_plexus_workbench/static/
```

`python scripts/check-tools-sabotage.py` checks that removing approval,
ownership, revocation and interrupted-call guards breaks the behavioral tests,
then restores the exact working files. Run it alone. For system Chrome coverage,
build the page and set `WORKBENCH_PLAYWRIGHT` to a `playwright-core` installation
before running pytest; `WORKBENCH_CHROME` can override the browser executable.

The built page is gitignored on `main`. Eugene's app catalogue installs from a commit on the **`dist`** branch, which is `main` plus that build. A GitHub archive of `main` alone would install with no page.

To make a `dist` commit from a `main` commit:

1. Check out `dist` beside a clean `main`.
2. Replace its tree with `main`'s files and the build (`git archive` of `main`, then `src/eugene_plexus_workbench/static/`, added with `git add -f`).
3. Write `BUILD_INFO` as `built-from: eugene-plexus/workbench@<main commit>`.
4. **Put `src/eugene_plexus_workbench/_build.py` back as `main` has it.** `git archive` stamps the commit into it. Left stamped, an archive of `dist` would name `main`'s commit instead of its own.
5. Commit, push `dist`, and point the agent's `apps_catalogue.yaml` at the new commit.

The config-trio shapes are generated from [`eugene-plexus/specs`](https://github.com/eugene-plexus/specs) at the commit in `SPECS_REF`. Run `python scripts/codegen.py` to regenerate them.

## Licence

Apache 2.0. Eugene's mascot and logo come from the project's website and are under the same licence.
