"""A removed person's data goes with them (row 7, `specs/docs/design/rows/row-07.md`).

**How Workbench finds out.** Eugene's `POST /oidc/subjects` says, of the people who have signed
in here, which it still has. Workbench asks at start, every hour, when the owner opens Settings
or presses Check now, and a minute after Eugene refuses a sign-in's refresh. A person is `known`
when Eugene has a person record (turned on or off) and `unknown` when it was deleted. The owner
is never asked about, and never removed. A sign-in, or a refresh, says Eugene has the person
(`people.known_at`).

**What an answer does, and what it never does.** Nothing is deleted on a guess:

- a person Eugene confirmed before (`known_at`) and now does not have is hidden, and their
  delete date is `removedPeopleDays` (30) from now;
- a person Eugene never confirmed (they signed in before this update) is `held`: hidden, with
  no delete date, until the owner chooses;
- three or more unknown in one answer, or any after Workbench was pointed at another Eugene,
  hide nobody: the owner is asked (the brake), and only "They were removed" hides them, after
  Eugene is asked again;
- an answer that cannot be had, or read, changes nothing, and says why;
- a person Eugene has again (a root restored from a backup) is no longer removed. A purge that
  has begun finishes.

**Hiding** is one transaction (`Store.hide_people`): their sign-ins, their connections to other
apps and what waited to go to their memory server go. Then their running answers stop, their
index is closed and may not be opened or made again, and their folders are no longer checked.
Every read leaves out a hidden person's data (`store._HIDDEN`), the owner's included.

**The purge** (Delete now, or the date passed) commits `purging` before it changes anything,
records each step it finishes in `steps`, and is resumed after a restart. It is a true delete
(Troy, MRC27): when it ends no file Workbench keeps holds the person's data. Its steps:

1. `memory`: their own memory server is asked to forget each exchange Workbench sent it, never
   to purge (it may hold other apps' memories). Three failures in a row end the step, and the
   cause is recorded: what the server keeps stays there.
2. `knowledge` and 3. `files`: their directories, renamed out of the way and removed.
4. `exports`: every download of theirs, or of them.
5. `store`: every row `store.PERSON_TABLES` names, then the file written again.
6. `copies`: the same, in every `*.schema-N.bak` copy, which stays for everyone else.

A step that fails is said in words (`last_error`) and tried again at the next round or on Try
again; the others go on, so as much as can go goes at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, StrictBool

from . import config, files
from .signin import Provider, SignInUnavailable
from .store import HIDDEN_STATES, Person, Store

log = logging.getLogger(__name__)

router = APIRouter()

#: How often Workbench asks Eugene, and how soon after Eugene refused a sign-in's refresh.
CHECK_SECONDS = 3600.0
AFTER_REFUSAL_SECONDS = 60.0
#: How recent a check is good enough when the owner opens Settings.
FRESH_SECONDS = 60.0
#: How many unknown people in one answer stop Workbench hiding anyone (the brake).
BRAKE = 3
#: The most ids one question to Eugene carries.
BATCH = 500
DAY = 86_400.0
#: Consecutive failures that end the memory server step.
FORGET_TRIES = 3
#: Tries at moving a directory that Windows says another program holds open.
MOVE_TRIES = 5

STEPS = ("memory", "knowledge", "files", "exports", "store", "copies")
#: What each step's failure leaves of the person's data, for the sentence that says so.
_LEFT = {
    "knowledge": "their documents",
    "files": "their files",
    "exports": "their downloads",
    "store": "their chats and notes",
    "copies": "the copies kept from earlier updates",
}

BRAKE_KEY = "removedPeopleBrake"
ISSUER_KEY = "removedPeopleIssuer"
CHECKED_KEY = "removedPeopleCheckedAt"
PROBLEM_KEY = "removedPeopleProblem"


def _cause(exc: BaseException) -> str:
    if isinstance(exc, PermissionError) and getattr(exc, "winerror", None) in (5, 32):
        return "another program has one of the files open"
    if isinstance(exc, OSError):
        return exc.strerror or type(exc).__name__
    return str(exc) or type(exc).__name__


def _writable(function: Callable[..., Any], target: str, _excinfo: Any) -> None:
    """`rmtree`'s retry for a read-only file (Windows will not delete one)."""
    os.chmod(target, stat.S_IWRITE)
    function(target)


def remove_tree(directory: Path) -> None:
    """Remove a person's directory: renamed out of the way first (`.purge-<name>`), so nothing
    that looks for the person finds a half-removed one, then deleted. A rename Windows refuses
    because a program holds a file open is tried again a few times, then raised. A directory
    a stop left half removed is finished."""
    staged = directory.with_name(f".purge-{directory.name}")
    if directory.exists():
        for attempt in range(MOVE_TRIES):
            try:
                if staged.exists():
                    shutil.rmtree(staged, onexc=_writable)
                os.replace(directory, staged)
                break
            except PermissionError:
                if attempt == MOVE_TRIES - 1:
                    raise
                time.sleep(0.2 * (attempt + 1))
    if staged.exists():
        shutil.rmtree(staged, onexc=_writable)


class Removal:
    """Asks Eugene who was removed, hides their data, and deletes it. One per Workbench."""

    def __init__(
        self,
        store: Store,
        provider: Provider,
        data_dir: Path,
        *,
        saved: Any = None,
        answers: Any = None,
        folder_sync: Any = None,
        servers: Any = None,
        exports: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.provider = provider
        self.data_dir = data_dir
        self.saved = saved
        self.answers = answers
        self.folder_sync = folder_sync
        self.servers = servers
        self.exports = exports
        self.clock = clock
        self._asking = asyncio.Lock()
        self._purges: dict[str, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._later: asyncio.TimerHandle | None = None

    # --- running -----------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="removed-people")

    def soon(self) -> None:
        """Eugene refused a sign-in's refresh: ask it about everyone in a minute."""
        if self._later is None:
            self._later = asyncio.get_running_loop().call_later(
                AFTER_REFUSAL_SECONDS, self._wake.set
            )

    async def aclose(self) -> None:
        if self._later is not None:
            self._later.cancel()
        tasks = [t for t in (self._task, *self._purges.values()) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self) -> None:
        for row in await self.store.removed_people():
            if row["state"] == "purging":
                self.purge_soon(row["sub"])
        while True:
            self._later = None
            try:
                await self.check()
                await self.due()
                if self.exports is not None:
                    await self.exports.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("checking for people removed from Eugene failed in Workbench itself")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=CHECK_SECONDS)

    # --- asking Eugene -------------------------------------------------------

    async def _ask(self, subs: list[str]) -> tuple[list[str], list[str]]:
        known: list[str] = []
        unknown: list[str] = []
        for start in range(0, len(subs), BATCH):
            k, u = await self.provider.subjects(subs[start : start + BATCH])
            known += k
            unknown += u
        return known, unknown

    async def checked_at(self) -> float | None:
        value = await self.store.setting(CHECKED_KEY)
        return float(value) if value is not None else None

    async def check(self) -> None:
        """Ask Eugene about everyone who signed in here, and act on the answer (the module)."""
        async with self._asking:
            people = await self.store.people_to_ask()
            removed = {
                r["sub"]: r
                for r in await self.store.removed_people()
                if r["state"] in ("held", "hidden", "kept")
            }
            asked = [p["sub"] for p in people] + list(removed)
            now = self.clock()
            if not asked:
                await self.store.put_setting(CHECKED_KEY, now)
                await self.store.put_setting(PROBLEM_KEY, None)
                await self.store.put_setting(BRAKE_KEY, None)
                return
            try:
                known, unknown = await self._ask(asked)
            except SignInUnavailable as exc:
                problem = f"Workbench could not ask Eugene who was removed: {exc}."
                await self.store.put_setting(PROBLEM_KEY, problem)
                log.warning("%s", problem)
                return
            await self.store.put_setting(PROBLEM_KEY, None)
            await self.store.put_setting(CHECKED_KEY, now)
            await self.store.confirm_known(known, now)
            for sub in await self.store.back_from_removed([s for s in known if s in removed]):
                self._allow(sub)
                log.info("Eugene has %s again: their data is theirs again", removed[sub]["name"])
            lost = set(unknown)
            gone = [p for p in people if p["sub"] in lost]
            brake = await self.store.setting(BRAKE_KEY)
            braked = set((brake or {}).get("subs") or [])
            issuer = await self.store.setting(ISSUER_KEY)
            moved = issuer is not None and issuer != self.provider.issuer
            await self.store.put_setting(ISSUER_KEY, self.provider.issuer)
            if len(gone) >= BRAKE or (moved and gone) or (braked & {p["sub"] for p in gone}):
                subs = sorted(p["sub"] for p in gone)
                same = set(subs) == braked
                await self.store.put_setting(
                    BRAKE_KEY,
                    {
                        "subs": subs,
                        "names": {p["sub"]: p["name"] for p in gone},
                        "foundAt": (brake or {}).get("foundAt") if same else now,
                        "dismissed": bool((brake or {}).get("dismissed")) and same,
                        "moved": moved or (bool((brake or {}).get("moved")) and same),
                    },
                )
                log.warning(
                    "Eugene no longer knows %d people who use Workbench; nothing was hidden "
                    "until the owner says they were removed",
                    len(subs),
                )
                return
            if brake is not None:
                await self.store.put_setting(BRAKE_KEY, None)
            await self.hide(gone)

    async def hide(self, people: list[dict[str, Any]]) -> list[str]:
        """Hide these people's data now: with a delete date when Eugene had confirmed them,
        held with none when it never had. When the owner keeps a removed person's data "Not at
        all" (`removedPeopleRetention`), a confirmed removal is deleted at once, by the same
        purge as Delete now; a held one still waits for the owner (nothing on a guess)."""
        at_once = await config.removed_people_retention(self.store) == config.RETENTION_NONE
        now = self.clock()
        entries = [
            (
                p["sub"],
                p["name"],
                "hidden" if p.get("known_at") is not None else "held",
                now if p.get("known_at") is not None else None,
            )
            for p in people
            if p["sub"] != "operator"
        ]
        done = await self.store.hide_people(entries)
        names = {p["sub"]: p["name"] for p in people}
        confirmed = {sub for sub, _, state, _ in entries if state == "hidden"}
        for sub in done:
            await self.stop(sub)
            if at_once and sub in confirmed:
                await self.delete_now(sub)
                log.info("%s was removed from Eugene: their data is being deleted", names[sub])
            else:
                log.info(
                    "%s was removed from Eugene: their data in Workbench is hidden", names[sub]
                )
        return done

    async def stop(self, sub: str) -> None:
        """Everything running for the person ends, and their index is closed for good."""
        indexes = getattr(self.saved, "indexes", None)
        if indexes is not None:
            indexes.refuse(sub)
            await indexes.forget(sub)
        if self.answers is not None:
            for chat_id in await self.store.person_chat_ids(sub):
                if self.answers.running(chat_id) is not None:
                    await self.answers.stop(chat_id)
        if self.folder_sync is not None:
            await self.folder_sync.stop_person(sub)

    def _allow(self, sub: str) -> None:
        indexes = getattr(self.saved, "indexes", None)
        if indexes is not None:
            indexes.allow(sub)

    async def signed_in(self, sub: str) -> str | None:
        """Someone signed in, so Eugene has them: if they were removed, they are not any more.
        Why the sign-in cannot go on, while their data is being deleted; else None."""
        row = await self.store.removed_person(sub)
        if row is None:
            return None
        if row["state"] == "purging":
            return (
                "Your data in this Workbench is being deleted, because Eugene had removed you. "
                "Sign in again in a few minutes."
            )
        if row["state"] in ("held", "hidden", "kept"):
            await self.store.back_from_removed([sub])
            log.info("%s signed in again: their data is theirs again", row["name"])
        self._allow(sub)
        return None

    # --- the brake ------------------------------------------------------------

    async def settle_brake(self, removed: bool) -> None:
        """The owner's answer to the brake. "They were removed" asks Eugene once more and hides
        whoever it still does not have; "Not now" leaves everyone as they are and the line
        quiet until the people in question change."""
        brake = await self.store.setting(BRAKE_KEY)
        if brake is None:
            return
        if not removed:
            await self.store.put_setting(BRAKE_KEY, {**brake, "dismissed": True})
            return
        async with self._asking:
            wanted = set(brake.get("subs") or [])
            people = [p for p in await self.store.people_to_ask() if p["sub"] in wanted]
            if people:
                _, unknown = await self._ask([p["sub"] for p in people])
                gone = [p for p in people if p["sub"] in set(unknown)]
                await self.hide(gone)
            await self.store.put_setting(BRAKE_KEY, None)

    # --- deleting --------------------------------------------------------------

    async def due(self) -> list[str]:
        """Start the purge of everyone whose delete date has passed."""
        days = await config.removed_people_days(self.store)
        now = self.clock()
        started = []
        for row in await self.store.removed_people():
            if row["state"] == "hidden" and row["clock_from"] is not None:
                if row["clock_from"] + days * DAY <= now:
                    self.purge_soon(row["sub"])
                    started.append(row["sub"])
            elif row["state"] == "purging":
                self.purge_soon(row["sub"])
        return started

    def purge_soon(self, sub: str) -> None:
        """Run the person's purge in the background, once at a time."""
        running = self._purges.get(sub)
        if running is not None and not running.done():
            return
        task = asyncio.create_task(self.purge(sub), name=f"purge-{sub[:8]}")
        self._purges[sub] = task
        task.add_done_callback(
            lambda t: self._purges.pop(sub, None) if self._purges.get(sub) is t else None
        )

    async def delete_now(self, sub: str) -> bool:
        """The owner's Delete now: `purging` is committed before this returns."""
        if not await self.store.put_removed(
            sub, only_from=("held", "hidden", "kept", "purging"), state="purging"
        ):
            return False
        self.purge_soon(sub)
        return True

    async def purge(self, sub: str) -> None:
        """Delete the person's data, step by step (the module). Resumed where it stopped."""
        row = await self.store.removed_person(sub)
        if row is None or row["state"] not in ("hidden", "purging"):
            return
        # The date passed: `purging` is committed first, and only from `hidden`, so a Keep the
        # owner pressed a moment before wins.
        if row["state"] == "hidden" and not await self.store.put_removed(
            sub, only_from=("hidden",), state="purging", last_error=None
        ):
            return
        name, steps = str(row["name"]), dict(row["steps"])
        await self.stop(sub)
        failed: dict[str, str] = {}
        for step in STEPS:
            # The memory step is not tried twice: what it could not clear is said, and stays.
            if steps.get(step) == "done" or (step == "memory" and "memory" in steps):
                continue
            try:
                outcome = await getattr(self, f"_step_{step}")(sub)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed[step] = _cause(exc)
                log.warning("deleting a removed person's data: %s failed: %s", step, failed[step])
                continue
            steps[step] = outcome
            await self.store.put_removed(sub, steps=steps)
        memory = steps.get("memory", "done")
        note = (
            f"{name}'s memory server was not cleared: {memory}. What it keeps stays there."
            if memory != "done"
            else None
        )
        if failed:
            left = " and ".join(_LEFT[s] for s in STEPS if s in failed)
            first = next(iter(failed.values()))
            await self.store.put_removed(
                sub,
                last_error=f"{name}'s data was deleted except {left}: {first}. Try again.",
                attempts=int(row["attempts"]) + 1,
            )
            return
        # The email was kept to give the data back (row 7b); with the data gone, it goes too.
        await self.store.put_removed(
            sub, state="purged", purged_at=self.clock(), last_error=note, email=None
        )
        log.info("%s's data was deleted from Workbench", name)

    async def _step_memory(self, sub: str) -> str:
        """Ask the person's own memory server to forget what Workbench sent it."""
        from .memory_servers import ServerProblem, mode_for

        server = await self.store.memory_server(sub)
        owed = await self.store.forgets_owed(sub)
        if server is None or not owed:
            return "done"
        mode, why_not = await mode_for(self.store)
        if mode == "off":
            return why_not or "this install does not allow memory servers"
        if "forget" not in (server.get("verbs") or {}) or self.servers is None:
            return "it has no forget tool"
        misses, last = 0, ""
        for row in owed:
            try:
                await self.servers.call(server, mode, "forget", {"document_id": row["document_id"]})
            except ServerProblem as exc:
                misses, last = misses + 1, exc.message
                if misses >= FORGET_TRIES:
                    return last.rstrip(".")
                continue
            misses = 0
            await self.store.memory_server_drop(row["document_id"])
        return "done"

    async def _step_knowledge(self, sub: str) -> str:
        indexes = getattr(self.saved, "indexes", None)
        if indexes is not None:
            indexes.refuse(sub)
            await indexes.forget(sub)
        await asyncio.to_thread(remove_tree, self.data_dir / "knowledge" / files.person_key(sub))
        return "done"

    async def _step_files(self, sub: str) -> str:
        await asyncio.to_thread(remove_tree, files.person_dir(self.data_dir, sub))
        return "done"

    async def _step_exports(self, sub: str) -> str:
        if self.exports is not None:
            await self.exports.drop(sub)
        return "done"

    async def _step_store(self, sub: str) -> str:
        await self.store.purge_person(sub)
        return "done"

    async def _step_copies(self, sub: str) -> str:
        await self.store.purge_copies(sub)
        return "done"

    # --- what the owner sees ----------------------------------------------------

    async def view(self) -> dict[str, Any]:
        days = await config.removed_people_days(self.store)
        brake = await self.store.setting(BRAKE_KEY)
        log_rows = await self.store.export_log()
        from .exports import view as export_view

        return {
            "retention": await config.removed_people_retention(self.store),
            "days": days,
            "people": [row_view(r, days) for r in await self.store.removed_people()],
            "brake": (
                {
                    "count": len(brake.get("subs") or []),
                    "names": sorted((brake.get("names") or {}).values()),
                    "dismissed": bool(brake.get("dismissed")),
                    "otherEugene": bool(brake.get("moved")),
                }
                if brake
                else None
            ),
            "checkedAt": await self.checked_at(),
            "problem": await self.store.setting(PROBLEM_KEY),
            "exportLog": [export_view(r) for r in log_rows],
        }


def row_view(row: dict[str, Any], days: int) -> dict[str, Any]:
    """A removed person as Settings shows them. The delete date is worked out from the setting
    in effect now, so it never says a date the setting no longer gives."""
    hidden = row["state"] == "hidden" and row["clock_from"] is not None
    return {
        "sub": row["sub"],
        "name": row["name"],
        "state": row["state"],
        "removedAt": row["found_at"],
        "deleteAt": row["clock_from"] + days * DAY if hidden else None,
        "purgedAt": row["purged_at"],
        "problem": row["last_error"],
    }


# --------------------------------------------------------------------------- #
# the owner's routes
# --------------------------------------------------------------------------- #


def _problem(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"message": message})


async def _owner(request: Request) -> tuple[Person, Removal]:
    person: Person = await request.app.state.sessions.person(request)
    if not person.is_owner:
        raise _problem(status.HTTP_403_FORBIDDEN, "Only the owner of this install does this.")
    return person, request.app.state.removal


@router.get("/api/removed-people")
async def removed_people(request: Request) -> dict[str, Any]:
    """The people removed from Eugene, what happens to their data and when, and the log of
    downloads the owner made. Opening it asks Eugene again when it was not asked lately."""
    _, removal = await _owner(request)
    last = await removal.checked_at()
    if last is None or time.time() - last > FRESH_SECONDS:
        removal._wake.set()
    return await removal.view()


@router.post("/api/removed-people/check")
async def check_now(request: Request) -> dict[str, Any]:
    """Ask Eugene now, and start any purge that is due."""
    _, removal = await _owner(request)
    await removal.check()
    await removal.due()
    return await removal.view()


async def _removed_row(removal: Removal, sub: str) -> dict[str, Any]:
    row = await removal.store.removed_person(sub)
    if row is None:
        raise _problem(status.HTTP_404_NOT_FOUND, "That person is not removed from Eugene.")
    return row


@router.post("/api/removed-people/{sub}/keep")
async def keep(request: Request, sub: str) -> dict[str, Any]:
    """Keep: stop the clock. Nothing is given back to anyone; the data stays hidden."""
    _, removal = await _owner(request)
    row = await _removed_row(removal, sub)
    if not await removal.store.put_removed(
        sub, only_from=("held", "hidden", "kept"), state="kept", clock_from=None
    ):
        raise _problem(status.HTTP_409_CONFLICT, f"{row['name']}'s data is being deleted.")
    log.info("the owner kept %s's data until they delete it", row["name"])
    return row_view(
        await _removed_row(removal, sub), await config.removed_people_days(removal.store)
    )


@router.post("/api/removed-people/{sub}/schedule")
async def schedule(request: Request, sub: str) -> dict[str, Any]:
    """Delete in N days: the clock starts now."""
    _, removal = await _owner(request)
    row = await _removed_row(removal, sub)
    if not await removal.store.put_removed(
        sub, only_from=("held", "hidden", "kept"), state="hidden", clock_from=time.time()
    ):
        raise _problem(status.HTTP_409_CONFLICT, f"{row['name']}'s data is being deleted.")
    days = await config.removed_people_days(removal.store)
    log.info("the owner set %s's data to be deleted in %d days", row["name"], days)
    return row_view(await _removed_row(removal, sub), days)


@router.post("/api/removed-people/{sub}/delete", status_code=status.HTTP_202_ACCEPTED)
async def delete_now(request: Request, sub: str) -> dict[str, Any]:
    """Delete now, at once and for good: every copy Workbench keeps (MRC27). Also Try again."""
    _, removal = await _owner(request)
    row = await _removed_row(removal, sub)
    if row["state"] == "purged":
        raise _problem(status.HTTP_409_CONFLICT, f"{row['name']}'s data is already deleted.")
    await removal.delete_now(sub)
    log.info("the owner deleted %s's data now", row["name"])
    return row_view(
        await _removed_row(removal, sub), await config.removed_people_days(removal.store)
    )


class BrakeAnswer(BaseModel):
    removed: StrictBool


@router.post("/api/removed-people/brake")
async def brake(request: Request, body: BrakeAnswer) -> dict[str, Any]:
    """The owner's answer to "Eugene no longer knows N people": they were removed, or not now."""
    _, removal = await _owner(request)
    try:
        await removal.settle_brake(body.removed)
    except SignInUnavailable as exc:
        raise _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Workbench could not ask Eugene who was removed: {exc}. Nothing was hidden.",
        ) from None
    return await removal.view()


__all__ = ["HIDDEN_STATES", "Removal", "remove_tree", "router", "row_view"]
