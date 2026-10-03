# Workbench

Eugene Plexus's own chat app. It lets you chat with the models your Eugene install serves, and search the web while you do. It signs people in with Eugene. One person can use it with Eugene's passphrase, or a small business can use it with an account for each person.

Workbench is an *app* (a spoke), not part of the hub. It reaches Eugene the same way any other app does:

- one client key, used at the gateway's public OpenAI-compatible endpoints;
- sign-in with Eugene over OpenID Connect.

It holds nothing in the hub that another app could not be given. The design is [`specs/docs/design/workbench-v1.md`](https://github.com/eugene-plexus/specs/blob/main/docs/design/workbench-v1.md).

## Installing

Install Workbench from **Apps** in Eugene's console. The install does three things:

- it makes Workbench's key;
- it registers Workbench to sign people in with Eugene;
- it runs Workbench in an OS account of its own, where the install can make one.

Open it from the same page.

Workbench's one setting is on its page in the console. It controls whether the owner may read the chats of the people the owner gives Workbench to. It is off by default.

## What it does

- **Chats**: answers stream as they arrive. You can Stop an answer, Try again, or edit a message.
- **Markdown and code**: the model's reasoning is shown folded away.
- **Attachments**: images (PNG or JPEG), PDFs and audio (WAV or MP3). Each goes to a model that takes that kind of file.
- **Tools: Search the web**: this runs on the install's search account. When a search cannot run, the switch says why.
- **MCP tools**: the owner adds shared Streamable HTTP servers in **Toolbox · Tools**. Choose servers in a chat's settings, then approve or decline each proposed call. Credentials stay on the server. Pending approvals expire after 30 minutes; interrupted calls are never automatically repeated.
- **Answers keep going with no tab open**: an answer is saved as it arrives. Close the tab and come back, and it is there.

MCP connections use HTTPS, except for loopback HTTP, with an optional bearer credential. They are shared with everyone signed into this Workbench. Tool results and arguments stay with the answer. Stopping or editing a chat does not undo actions already taken. Local MCP commands, OAuth sign-in to MCP servers, folder grants, and media screens are later slices; see the [MCP design](https://github.com/eugene-plexus/specs/blob/main/docs/design/workbench-mcp.md).

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
