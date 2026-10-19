"""Sabotage MR9b's critical checks once each: every mutation must turn `tests/test_connections.py`
red. Each file is restored from a copy taken before it is changed, never from git.

Run with the Workbench interpreter from the repo root:
  .venv/Scripts/python.exe scripts/mr9-connections-sabotage.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "eugene_plexus_workbench"

#: (what is broken, file, the code, what it becomes)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the rights check when tools are listed",
        "connections.py",
        "return [tool for name, tool in _TOOLS.items() if getattr(connection, _RIGHTS[name])]",
        "return list(_TOOLS.values())",
    ),
    (
        "the token lookup (any wbc_ token is a connection)",
        "connections.py",
        "    record = await store.connection_by_token(token_hash(token))\n",
        "    record = await store.connection_by_token(token_hash(token))\n"
        "    if record is None:\n"
        "        found = await store.connections('p-ada')\n"
        "        record = found[0] if found else None\n",
    ),
    (
        "the sign-in binding",
        "connections.py",
        "        person = await sessions.for_connection(record.owner)",
        "        person = await sessions._store.person(record.owner)",
    ),
    (
        "the Origin check",
        "connections.py",
        "if origin is not None and origin != own_origin(request):",
        "if False:",
    ),
    (
        "the token-in-the-address refusal",
        "connections.py",
        'if scope.get("query_string"):',
        "if False:",
    ),
    (
        "the install switch",
        "connections.py",
        "    if not await config.connections_enabled(store):\n        raise Refused(403, TURNED_OFF)\n",  # noqa: E501
        "",
    ),
    (
        "the expiry check",
        "connections.py",
        "    if time.time() >= record.expires_at:",
        "    if False:",
    ),
    (
        "a suggestion stays pending",
        "store.py",
        "\"VALUES (?, ?, ?, ?, 'pending', 0, 'suggested', NULL, ?, ?, ?, ?)\",",
        "\"VALUES (?, ?, ?, ?, 'active', 0, 'suggested', NULL, ?, ?, ?, ?)\",",
    ),
    (
        "revoking takes the unkept suggestions",
        "store.py",
        "                        \"DELETE FROM memory_notes WHERE owner = ? AND status = 'pending' \"\n"  # noqa: E501
        '                        "AND source_connection_id = ?",',
        '                        "DELETE FROM memory_notes WHERE owner = ? AND 0 "\n'
        '                        "AND source_connection_id = ?",',
    ),
    (
        "the live-connection check inside the suggestion's write",
        "store.py",
        '                if live is None:\n                    db.execute("COMMIT")\n                    return "revoked"\n',  # noqa: E501
        "",
    ),
    (
        "revocation is the owner's alone",
        "store.py",
        '"DELETE FROM connections WHERE id = ? AND owner = ?", (connection_id, owner)',
        '"DELETE FROM connections WHERE id = ? OR owner = ?", (connection_id, owner)',
    ),
    (
        "a connection's list is its owner's",
        "store.py",
        '"SELECT * FROM connections WHERE owner = ? ORDER BY created_at DESC, id",',
        '"SELECT * FROM connections WHERE owner = owner OR ? ORDER BY created_at DESC, id",',
    ),
    (
        "the secret check on a suggestion",
        "connections.py",
        '            text = memory.validate_note(unicodedata.normalize("NFKC", raw))',
        '            text = unicodedata.normalize("NFKC", raw)',
    ),
]


def run_tests() -> bool:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_connections.py",
            "-q",
            "-x",
            "-p",
            "no:warnings",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return done.returncode == 0


def main() -> int:
    if not run_tests():
        print("the tests fail before any sabotage; fix them first")
        return 2
    caught = 0
    for what, name, old, new in MUTATIONS:
        path = SRC / name
        copy = path.with_suffix(path.suffix + ".sabotage-copy")
        shutil.copyfile(path, copy)
        try:
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"SKIP  {what}: the code to break was not found once in {name}")
                continue
            path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
            green = run_tests()
        finally:
            shutil.copyfile(copy, path)
            copy.unlink()
        if green:
            print(f"MISS  {what}: the tests stayed green")
        else:
            caught += 1
            print(f"CAUGHT  {what}")
    print(f"\n{caught} of {len(MUTATIONS)} caught")
    return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
