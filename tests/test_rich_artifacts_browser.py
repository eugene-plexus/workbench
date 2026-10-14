"""Artifacts and their previews in Chrome, under the headers Workbench really
sends (workbench-rich-content-roadmap.md RC5). Opt-in, like the other browser
checks; set WORKBENCH_SHOTS to keep the pictures."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eugene_plexus_workbench.web import CSP, PREVIEW_CSP

from .conftest import World
from .test_search_suggestions import GOOGLE


def fence(language: str, title: str, body: str) -> str:
    return f'```{language} artifact title="{title}"\n{body}\n```'


NOTES = "# Shopping\n\n- bread\n- milk\n\nKept **as written**."
LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><title>Logo</title>'
    '<rect width="40" height="20" fill="#3a6"/><circle cx="10" cy="10" r="6" fill="#fff"/></svg>'
)
SCRIPTED = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9">'
    "<script>parent.document.title='svg got out'</script><rect width='9' height='9'/></svg>"
)
# Every way out it might try, each written down in its own page as it fails or not.
ESCAPES = """<!doctype html>
<html><head><title>Escapes</title></head><body>
<ul id="out"></ul>
<form id="f" method="post" action="/api/chats"><input name="x" value="1">
<button id="b" type="submit">Send</button></form>
<script>
function say(name, what) {
  var li = document.createElement("li"); li.dataset.name = name; li.textContent = name + ": " + what;
  document.getElementById("out").appendChild(li);
}
function attempt(name, fn) {
  try { var r = fn(); say(name, r === null || r === undefined ? "blocked" : "allowed " + String(r).slice(0, 40)); }
  catch (e) { say(name, "blocked " + e.name); }
}
attempt("parent-dom", function () { return parent.document.title; });
attempt("top-dom", function () { return top.document.body.innerHTML; });
attempt("cookie", function () { return document.cookie || null; });
attempt("storage", function () { return localStorage.length; });
attempt("top-navigation", function () { top.location.href = "https://example.com/top"; return "tried"; });
attempt("popup", function () { return window.open("https://example.com/popup"); });
try {
  var w = new Worker(URL.createObjectURL(new Blob(["postMessage('ran')"])));
  w.onmessage = function () { say("worker", "allowed ran"); };
  w.onerror = function () { say("worker", "blocked error"); };
} catch (e) { say("worker", "blocked " + e.name); }
attempt("service-worker", function () { return navigator.serviceWorker.register("/sw.js"); });
fetch("/api/me").then(function (r) { say("api", "allowed " + r.status); }, function (e) { say("api", "blocked " + e.name); });
fetch("https://example.com/out").then(function () { say("network", "allowed"); }, function (e) { say("network", "blocked " + e.name); });
new Image().src = "/api/me?from=img";
// By script (refused before Chrome sees it), and by its button (stopped as it is sent).
say("form-script", /\\[native code\\]/.test(Function.prototype.toString.call(HTMLFormElement.prototype.submit)) ? "allowed" : "blocked");
document.getElementById("f").submit();
addEventListener("submit", function (e) { say("form-button", e.defaultPrevented ? "blocked" : "allowed"); });
document.getElementById("b").click();
var s = document.createElement("script"); s.src = "https://cdn.example.com/lib.js"; document.head.appendChild(s);
say("done", "yes");
</script>
</body></html>"""
BUSY = "<!doctype html><p>Busy</p><script>setTimeout(function () { for (;;) {} }, 50)</script>"
LEAVES = (
    "<!doctype html><p>Leaving</p>"
    "<script>setTimeout(function () { location.href = 'https://example.com/away'; }, 300)</script>"
)
COUNTER = """import { useState } from "react";

type Props = { start?: number };

export default function Counter({ start = 0 }: Props) {
  const [n, setN] = useState(start);
  const [name, setName] = useState("");
  const [names, setNames] = useState<string[]>([]);
  return (
    <div>
      <button onClick={() => setN(n + 1)}>Clicked {n}</button>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setNames([...names, name]);
          setName("");
        }}
      >
        <input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="submit">Add</button>
      </form>
      <ul>{names.map((x) => <li key={x}>{x}</li>)}</ul>
    </div>
  );
}"""
NEEDS = 'import _ from "lodash";\n\nexport default function List() {\n  return <p>{_.join(["a", "b"])}</p>;\n}'
PLAIN = "<p>A plain block</p>"

ANSWER = "\n\n".join(
    [
        "Here they are.",
        fence("markdown", "Notes", NOTES),
        fence("svg", "Logo", LOGO),
        fence("svg", "Scripted picture", SCRIPTED),
        fence("html", "Escapes", ESCAPES),
        fence("html", "Busy", BUSY),
        fence("html", "Leaves", LEAVES),
        fence("tsx", "Counter", COUNTER),
        fence("jsx", "Needs lodash", NEEDS),
        f"```html\n{PLAIN}\n```",
    ]
)


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_artifacts_and_their_previews_in_chrome(
    world: World, tmp_path: Path, quiet_loop: None
) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    ada = world.browser()
    ada.sign_in("p-ada")
    # RC5's checks are of previews that wait for Run: RC6's running by themselves
    # is tests/test_rich_artifacts_inline_browser.py's.
    assert ada.put("/api/me/run-previews", json={"on": False}).status_code == 200
    made = ada.new_chat()
    world.gateway.words = [ANSWER]
    sent = ada.post(f"/api/chats/{made}/messages", json={"content": "Make them"})
    assert sent.status_code == 201, sent.text
    ada.wait_answer(made)
    # A searched answer whose provider sent suggestions: their frame, beside the previews.
    world.gateway.words = ["Searched."]
    world.gateway.suggestions = [GOOGLE]
    searched = ada.new_chat()
    ada.post(f"/api/chats/{searched}/messages", json={"content": "News?", "search": True})
    ada.wait_answer(searched)
    world.gateway.suggestions = None
    world.gateway.words = [ANSWER]

    shots = Path(os.getenv("WORKBENCH_SHOTS") or tmp_path / "shots")
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "csp": CSP,
                "previewCsp": PREVIEW_CSP,
                "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
                "chrome": os.getenv(
                    "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
                ),
                "cookies": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "url": world.workbench,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for c in ada.http.cookies.jar
                ],
                "secret": ada.secret,
                "chats": {"made": made, "searched": searched},
                "sources": {"notes": NOTES, "plain": PLAIN},
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["node", str(root / "web/scripts/artifacts-browser.mjs"), str(cfg)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=420,
        )
    except subprocess.TimeoutExpired as exc:
        # What it said before it stopped, which says where.
        raise AssertionError(f"the browser run did not end:\n{exc.stdout}\n{exc.stderr}") from None
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
    # What the browser kept went through the API: the first version's notes, twice.
    tree = ada.get(f"/api/chats/{made}").json()["messages"]
    first = tree[-1]["versions"]
    assert first["count"] == 2, first
    kept = [
        a
        for via in first["ids"]
        for m in ada.get(f"/api/chats/{made}?via={via}").json()["messages"]
        for a in m.get("artifacts", [])
    ]
    assert [(a["title"], a["revisions"]) for a in kept] == [("Notes", 2)], kept
