"""Prove the checks of MR6b's acceptance can fail (`mr6b-sorting-acceptance.py`).

Each case breaks one thing in Workbench's result sorting, in a COPY of its source (the repo is
never touched, so there is nothing to restore: a hash of the whole tree before and after says so),
and runs the acceptance against the copy, over the stages that look at that thing. The named
checks must FAIL. A copy that does not compile, or a run whose stack, Workbench or index did not
come up, proves nothing and does not count as a caught defect. A control run of the same stages on
an unbroken copy must pass every check first: a check that fails only when the run is broken in
some other way is not a check.

The five the design names, and three more:
  the heading path dropped; the union of the two ways replaced by the hybrid's first ten; the
  budget ignored; a failure failing the search; automatic choosing a sorter outside the machines;
  and, besides, the sorter's order not applied, and the two defects the acceptance itself found (a
  gateway holding requests back for a moment taken for a sorter that is not running, and a sort
  that was given up on blended into what the sorter did while it was well).

  python scripts/check-mr6b-acceptance-sabotage.py --reuse-data DIR [--labels heading,budget]
  python scripts/check-mr6b-acceptance-sabotage.py --anchors      # only that each anchor is unique

`DIR` holds the index the run of record imported (the acceptance writes it after its import).
Run it with the Workbench interpreter, and with every ambient EUGENE_PLEXUS_* variable cleared.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
ACCEPTANCE = ROOT / "scripts" / "mr6b-sorting-acceptance.py"
SCRATCH = Path(
    os.environ.get(
        "EP_SCRATCH",
        "C:/Users/troyc/AppData/Local/Temp/claude/d--py-eugene-plexus-specs/"
        "8fdf4703-5901-4dea-a989-c9fe897eef3c/scratchpad",
    )
)
PKG = "eugene_plexus_workbench/"
SEARCH = PKG + "knowledge/search.py"
SORTING = PKG + "knowledge/sorting.py"

#: What a run must have got through for a failure in it to be the break's: the stack came up,
#: Workbench is the copy, and the index is the person's.
VALID = ("stack.listing", "stack.signin", "iso.source", "docs.ready", "docs.passages")


class Case:
    def __init__(
        self,
        label: str,
        edits: list[tuple[str, str, str]],
        stages: str,
        questions: int,
        expect: list[str],
    ) -> None:
        self.label = label
        self.edits = edits
        self.stages = stages
        self.questions = questions
        #: Checks of the acceptance that must fail; the first is the one that must.
        self.expect = expect


CASES = [
    Case(
        "the heading path is dropped (a passage is sent as its words alone)",
        [
            (
                SEARCH,
                "        embedders.passage_input(embedders.PLAIN, hit.heading_path, hit.text), hit.tokens_est",
                "        embedders.passage_input(embedders.PLAIN, None, hit.text), hit.tokens_est",
            )
        ],
        "gate,composition",
        6,
        ["gate.heading-form", "comp.union", "comp.heading", "gate.headings-present"],
    ),
    Case(
        "the union of the two ways is replaced by the hybrid's first ten",
        [
            (
                SEARCH,
                "    return [c for c in candidates if c.hit.chunk_id in wanted]",
                "    return list(candidates[:each])",
            )
        ],
        "gate,composition",
        6,
        ["comp.union", "gate.pool"],
    ),
    Case(
        "the budget is ignored (a sort that is too slow is made all the same)",
        [
            (
                SORTING,
                "        report = replace(report, rate=rate, expected=expected)\n        if expected > budget:",
                "        report = replace(report, rate=rate, expected=expected)\n        if False:",
            )
        ],
        "budget",
        3,
        ["budget.not-sorted", "budget.no-sort-asked", "budget.hybrid-order"],
    ),
    Case(
        "a failure of the sorter fails the search (nothing catches it)",
        [
            (
                SORTING,
                '        except (TimeoutError, HubError) as exc:\n            code, words = problem(self._error_of(exc, "the sort took too long"), model.id, budget)',
                '        except TimeoutError as exc:\n            code, words = problem(self._error_of(exc, "the sort took too long"), model.id, budget)',
            ),
            (
                SEARCH,
                "        except Exception as exc:  # a sorter's failure is never a search's",
                "        except ZeroDivisionError as exc:  # a sorter's failure is never a search's",
            ),
        ],
        "stopped",
        10,
        ["stop.mid-run", "stop.next", "stop.report"],
    ),
    Case(
        "automatic chooses a sorter outside the machines",
        [
            (
                SORTING,
                "        local = [m for m in models if m.local]\n        ready = [m for m in local if m.ready]",
                "        local = list(models)\n        ready = [m for m in local if m.ready]",
            )
        ],
        "outside",
        6,
        ["outside.auto-not-allowed", "outside.auto-allowed", "outside.auto-stopped"],
    ),
    Case(
        "a gateway holding requests back is said to be a sorter that is stopped (the defect the "
        "acceptance found: a 503 that asks for a wait is taken for a sorter that is not running)",
        [
            (
                SORTING,
                "    if exc.status == 503 and exc.retry_after is not None:",
                "    if False:",
            )
        ],
        "stopped",
        10,
        ["stop.holding"],
    ),
    Case(
        "a sort that is given up on is blended into what the sorter did while it was well (the "
        "weakness the acceptance found in a sorter that stops answering: the next question waits "
        "twice Sort time again)",
        [
            (
                SORTING,
                "                self._gave_up_on(model.id, tokens, limit)",
                "                self._record(model.id, tokens, limit)",
            )
        ],
        "hung",
        6,
        ["hung.not-again"],
    ),
    Case(
        "the sorter's order is not applied (the passages stay as the search found them)",
        [
            (
                SEARCH,
                "        return sorted_by(candidates, pool, done.order), done.report",
                "        return candidates, done.report",
            )
        ],
        "gate,composition",
        6,
        ["comp.order", "gate.places", "gate.order-applied"],
    ),
]

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
for key in [k for k in ENV if k.startswith("EUGENE_PLEXUS_")]:
    del ENV[key]
ENV.setdefault("WORKBENCH_PLAYWRIGHT", "D:/py/eugene-plexus/ui/node_modules/playwright")

LINE = re.compile(r"^\s+(PASS|FAIL)\s+\[([\w.\-]+)\]")


def tree_hash(path: Path) -> str:
    """One hash of every file under `path` (names and bytes), to show it was never touched."""
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(str(file.relative_to(path)).encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()


def anchors() -> None:
    for case in CASES:
        for name, before, _after in case.edits:
            text = (SRC / name).read_text(encoding="utf-8")
            found = text.count(before)
            if found != 1:
                raise SystemExit(f"{name}: the anchor for '{case.label}' is in {found} places.")
    print(f"{len(CASES)} cases, each anchor in exactly one place.")


def run_acceptance(
    source: Path, work: Path, out: Path, stages: str, questions: int, reuse: str
) -> dict[str, bool]:
    """The checks of one run of the acceptance, by name: True when it passed."""
    argv = [
        sys.executable,
        str(ACCEPTANCE),
        "--reuse-data",
        reuse,
        "--stages",
        stages,
        "--questions",
        str(questions),
        "--work",
        str(work),
    ]
    env = {**ENV, "EP_WORKBENCH": str(source)}
    started = time.perf_counter()
    ran = subprocess.run(
        argv,
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=2400,
        check=False,
    )
    out.write_text(ran.stdout + "\n--- stderr ---\n" + ran.stderr, encoding="utf-8", newline="\n")
    seen: dict[str, bool] = {}
    for line in ran.stdout.splitlines():
        m = LINE.match(line)
        if m:
            seen[m[2]] = seen.get(m[2], True) and m[1] == "PASS"
    print(
        f"    {len(seen)} checks in {time.perf_counter() - started:.0f} s; output {out}", flush=True
    )
    return seen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchors", action="store_true")
    parser.add_argument("--labels", default="", help="cases whose label has one of these words")
    parser.add_argument("--reuse-data", default="", help="the index the run of record kept")
    parser.add_argument("--no-control", action="store_true")
    args = parser.parse_args()
    anchors()
    if args.anchors:
        return 0
    if not args.reuse_data:
        raise SystemExit("--reuse-data DIR is needed: the index the run of record kept.")
    cases = CASES
    if args.labels:
        words = args.labels.split(",")
        cases = [c for c in CASES if any(w in c.label for w in words)]
        print(f"{len(cases)} cases chosen.")
    out = SCRATCH / "mr6b" / "sabotage"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    before = tree_hash(SRC)
    print(f"the repo's source before: {before[:16]}", flush=True)

    def make_copy(name: str) -> Path:
        copy = out / name
        shutil.copytree(
            SRC,
            copy / "src",
            ignore=shutil.ignore_patterns("__pycache__", "static"),
        )
        return copy

    # --- the control: the same stages, nothing broken ------------------------------------------
    if not args.no_control:
        stages = ",".join(sorted({s for c in cases for s in c.stages.split(",")}))
        questions = max(c.questions for c in cases)
        print(f"control: stages {stages}, {questions} questions, nothing broken", flush=True)
        copy = make_copy("control")
        seen = run_acceptance(
            copy,
            out / "control-work",
            out / "control.txt",
            stages,
            questions,
            args.reuse_data,
        )
        failed = [k for k, ok in seen.items() if not ok]
        if failed or not seen:
            print(f"the control run failed {failed}: see {out / 'control.txt'}")
            return 2
        print(f"control: all {len(seen)} checks pass.", flush=True)

    # --- the cases --------------------------------------------------------------------------
    caught = 0
    escaped: list[str] = []
    for n, case in enumerate(cases, start=1):
        print(f"\n[{n}/{len(cases)}] {case.label}", flush=True)
        name = f"case{n}"
        copy = make_copy(name)
        for rel, old, new in case.edits:
            path = copy / "src" / rel
            text = path.read_bytes().decode("utf-8")
            if text.count(old) != 1:
                raise SystemExit(f"{rel}: the anchor is in {text.count(old)} places in the copy.")
            path.write_bytes(text.replace(old, new).encode("utf-8"))
        build = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(copy / "src")],
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            print(
                f"    the broken copy does not compile: {build.stdout[-300:]}{build.stderr[-300:]}"
            )
            escaped.append(f"{case.label} (did not compile)")
            continue
        seen = run_acceptance(
            copy,
            out / f"{name}-work",
            out / f"{name}.txt",
            case.stages,
            case.questions,
            args.reuse_data,
        )
        invalid = [v for v in VALID if seen.get(v) is not True]
        failing = sorted(k for k, ok in seen.items() if not ok)
        wanted = [e for e in case.expect if seen.get(e) is False]
        ok = not invalid and case.expect[0] in failing
        print(f"    failing checks: {failing}", flush=True)
        if invalid:
            print(f"    the run did not get started properly: {invalid}", flush=True)
        print(
            f"{'CAUGHT' if ok else 'ESCAPED'}: {case.label} "
            f"(named checks that failed: {wanted} of {case.expect})",
            flush=True,
        )
        if ok:
            caught += 1
        else:
            escaped.append(case.label)
        shutil.rmtree(copy, ignore_errors=True)  # the broken copy is not kept
    after = tree_hash(SRC)
    print(
        f"\nthe repo's source after: {after[:16]} ({'unchanged' if after == before else 'CHANGED'})"
    )
    print(f"{caught}/{len(cases)} caught.")
    for label in escaped:
        print(f"  escaped: {label}")
    return 0 if caught == len(cases) and after == before else 1


if __name__ == "__main__":
    raise SystemExit(main())
