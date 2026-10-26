"""The page and the server agree about saved documents (MR4).

Workbench's own API is private and has no schema file to generate the page's types from,
so the page's TypeScript (`web/src/lib/types.ts`) is written by hand. These read it as
text and hold it to what the real server answers with, so a field the server renames, adds
or drops, or a state, a status or a reason it starts to say, is a failure here and not a
blank spot on a screen. `test_documents_page.py` does the same for the files attached to a
chat; this is the person's saved documents, their screen, a chat's Documents and the
passages under an answer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from eugene_plexus_workbench import api, context, saved_api
from eugene_plexus_workbench.knowledge import index as index_module
from eugene_plexus_workbench.knowledge.search import Retrieved

from . import documents as docs
from .conftest import MODEL, World, chat_model
from .saved import RARE, add, ask, chat_on, keep, listing, md, settings, start, survey_pdf
from .test_documents_page import LIB, interface

QUESTION = f"What is the code word for the lighthouse inspection? {RARE}"


def types() -> str:
    return (LIB / "types.ts").read_text(encoding="utf-8")


def body(name: str) -> str:
    found = re.search(
        rf"export interface {name}(?: extends \w+)? \{{\n(.*?)\n\}}", types(), re.DOTALL
    )
    assert found, f"types.ts has no interface {name}"
    return found.group(1)


def shape(name: str, field: str) -> set[str]:
    """The keys of the inline object type that interface `name` gives `field`, written
    over several lines or in one."""
    text = body(name)
    start = re.search(rf"^  {field}\??: \{{", text, re.MULTILINE)
    assert start, f"{name} has no object field {field}"
    depth = 0
    opened = start.end() - 1
    for at in range(opened, len(text)):
        if text[at] == "{":
            depth += 1
        elif text[at] == "}":
            depth -= 1
            if depth == 0:
                return set(re.findall(r"(\w+)\??: ", text[opened + 1 : at]))
    raise AssertionError(f"{name}.{field} is never closed")


def union(name: str) -> set[str]:
    """The strings of `export type NAME = "a" | "b"` in types.ts."""
    found = re.search(rf"export type {name} =(.*?);\n", types(), re.DOTALL)
    assert found, f"types.ts has no type {name}"
    return set(re.findall(r'"(\w+)"', found.group(1)))


def literals(name: str, field: str) -> set[str]:
    """The strings of an inline union that a field of interface `name` is: `mode: "a" | "b c";`.
    The union may end with `null` or with the name of another type (`mode: "a" | Sorted;`), which
    is not a string and is not one of these."""
    found = re.search(
        rf"^  {field}\??: ((?:\"[\w ]+\"(?:\s*\|\s*)?)+)(?:\w+)?;", body(name), re.MULTILINE
    )
    assert found, f"{name}.{field} is not a union of strings"
    return set(re.findall(r'"([\w ]+)"', found.group(1)))


def base_mode(mode: str) -> str:
    """How a search was done, without the sorter that put what it found in order (MR6b):
    `meaning and words, sorted by X` is `meaning and words`."""
    return mode.split(", sorted by ", 1)[0]


def server_text(name: str) -> str:
    return (Path(saved_api.__file__).parent / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# a document, and the screen's data
# --------------------------------------------------------------------------- #


def test_a_saved_document_has_the_fields_the_page_reads_in_every_state_it_can_be_in(
    world: World,
) -> None:
    ada = start(world)
    done = keep(
        ada,
        ("report.pdf", docs.three_pages()),
        ("scan.pdf", docs.pdf_pictures(2)),
        ("notes.md", md("Words.")),
    )
    page = interface("SavedDocument")
    states = {d["state"] for d in done["documents"]}
    assert {"ready", "skipped"} <= states
    for doc in done["documents"]:
        assert set(doc) == page, (set(doc) ^ page, doc["state"])
    skipped = next(d for d in done["documents"] if d["state"] == "skipped")
    # A skip says why, in the server's words, which the page shows as they came.
    assert skipped["message"].startswith("Skipped:") and skipped["code"]
    # Every state the server has is one the page has words for, and the other way round.
    assert union("SavedState") == set(index_module.STATES)


def test_a_file_that_was_added_has_the_fields_the_page_reads_and_so_has_a_refusal(
    world: World,
) -> None:
    ada = start(world)
    made = add(ada, ("notes.md", md("Words.")), ("again.md", md("More words.")))
    assert made.status_code == 201
    kept = made.json()["documents"]
    assert all(set(d) == interface("AddedDocument") | interface("SavedDocument") for d in kept)
    assert interface("AddedDocument") == {"unchanged", "replaced"}
    # Added again byte for byte: left; the same name with other bytes: replaced.
    again = add(ada, ("notes.md", md("Words.")), ("again.md", md("Other words."))).json()[
        "documents"
    ]
    assert [d["unchanged"] for d in again] == [True, False]
    assert [len(d["replaced"]) for d in again] == [0, 1]
    refused = add(ada, ("program.exe", b"MZ\x90\x00" + bytes(300)), ("notes2.md", md("Ok."))).json()
    assert set(refused["refused"][0]) == interface("AddRefusal")
    assert set(refused) == {"documents", "refused"}
    assert set(refused["refused"][0]) == {"name", "status", "message"}


def test_the_screens_data_has_the_fields_the_page_reads_down_to_each_number(world: World) -> None:
    ada = start(world)
    keep(ada, ("report.pdf", docs.three_pages()))
    found = listing(ada)
    assert set(found) == interface("DocumentsPage")
    assert set(found["totals"]) == shape("DocumentsPage", "totals")
    assert set(found["limits"]) == shape("DocumentsPage", "limits")
    assert set(found["search"]) == interface("SearchFacts")
    assert set(found["prefs"]) == interface("DocumentsPrefs")
    assert set(found["prefs"]["effective"]) == shape("DocumentsPrefs", "effective")
    # What the page says of the search is the server's own headline, for words only. This world's
    # gateway lists one model that only embeds and says nothing of where it runs, so Workbench
    # cannot place it, will not send a person's text to it, and says no search model is running.
    assert found["search"]["headline"] == "Searching by words only. No search model is running."
    assert found["search"]["code"] == "outside_only" and found["search"]["code"] in union(
        "SearchCode"
    )
    assert found["search"]["words"] is True and found["search"]["meaning"] is False
    assert set(found["totals"]["byState"]) == set(index_module.STATES)


def test_the_choices_about_models_outside_the_machines_are_the_ones_the_page_offers(
    world: World,
) -> None:
    ada = start(world)
    page = union("ExternalChoice")
    for choice in sorted(page):
        put = ada.put("/api/documents/prefs", json={"external": choice})
        assert put.status_code == 200, put.text
        assert put.json()["effective"]["external"] == choice
        assert put.json()["effective"]["externalSetBy"] == "you"
    assert ada.put("/api/documents/prefs", json={"external": "sometimes"}).status_code == 422
    default = ada.put("/api/documents/prefs", json={"external": None}).json()
    assert default["external"] is None and default["effective"]["externalSetBy"] == "default"
    # Whose word a setting is: the three the page has words for.
    settings(world, documentsExternalModels="never")
    owners = ada.get("/api/documents/prefs").json()
    assert owners["effective"]["externalSetBy"] == "owner"
    assert union("ExternalChoice") == {"never", "ask", "always"}


# --------------------------------------------------------------------------- #
# Test a question
# --------------------------------------------------------------------------- #


def test_a_question_that_was_tried_has_the_fields_the_page_reads_whatever_it_found(
    world: World,
) -> None:
    ada, bo = start(world), start(world, "p-bo")
    keep(ada, ("survey.pdf", survey_pdf(docs.CODE_WORD)))
    ran = {
        "found": ada.post("/api/documents/test", json={"question": "code word lighthouse"}),
        "nothing_matched": ada.post("/api/documents/test", json={"question": "zebra giraffe"}),
        "no_words": ada.post("/api/documents/test", json={"question": "?!"}),
        "no_documents": bo.post("/api/documents/test", json={"question": "code word"}),
        # Asked to search by meaning alone, with no search model to do it: none of the ways asked
        # for could run, which is said, and is never "nothing matched".
        "unavailable": ada.post(
            "/api/documents/test", json={"question": "code word lighthouse", "ways": ["meaning"]}
        ),
    }
    for wanted, response in ran.items():
        assert response.status_code == 200, response.text
        found = response.json()
        assert found["status"] == wanted
        assert set(found) == interface("TestedQuestion"), wanted
        assert set(found["query"]) == shape("TestedQuestion", "query")
        assert set(found["budget"]) == shape("TestedQuestion", "budget")
        assert set(found["searched"]) == interface("DocumentCounts")
        assert set(found["meaning"]) == interface("MeaningRun"), wanted
        assert base_mode(found["mode"]) in literals("TestedQuestion", "mode"), wanted
    assert ran["unavailable"].json()["mode"] == "none" and ran["found"].json()["mode"] == "words"
    assert literals("TestedQuestion", "status") == set(ran)
    hit = ran["found"].json()["passages"][0]
    assert set(hit) == interface("TestedPassage")
    assert set(hit["why"]) == shape("TestedPassage", "why")
    assert hit["name"] == "survey.pdf" and hit["pageStart"] == 4 and hit["via"] == "words"
    assert set(hit["why"]["terms"]) >= {"code", "word", "lighthouse"}


# --------------------------------------------------------------------------- #
# a chat's Documents
# --------------------------------------------------------------------------- #


def test_a_chats_documents_have_the_fields_the_page_reads_in_every_state(world: World) -> None:
    ada = start(world)
    keep(ada, ("report.pdf", docs.three_pages()))
    world.gateway.more_chat_models = [
        chat_model("outside", locality="external", owned_by="OpenRouter"),
        chat_model("tiny", locality="local", window=2048),
    ]
    page = interface("ChatDocuments")
    seen: dict[str, dict[str, Any]] = {}
    on = chat_on(ada)
    seen["searching"] = ada.get(f"/api/chats/{on}/documents").json()
    off = str(ada.post("/api/chats", json={"model": MODEL}).json()["id"])
    seen["chat_off"] = ada.get(f"/api/chats/{off}/documents").json()
    seen["outside_model"] = ada.get(f"/api/chats/{on}/documents?model=outside").json()
    seen["window_too_small"] = ada.get(f"/api/chats/{on}/documents?model=tiny").json()
    for name, view in seen.items():
        assert set(view) == page, (name, set(view) ^ page)
        assert set(view["searching"]) == shape("ChatDocuments", "searching"), name
        assert set(view["counts"]) == shape("ChatDocuments", "counts"), name
        assert set(view["counts"]["library"]) == interface("DocumentCounts"), name
        assert set(view["budget"]) == shape("ChatDocuments", "budget"), name
        assert set(view["search"]) == interface("SearchFacts"), name
    assert seen["searching"]["sending"] is True and seen["searching"]["reasonCode"] is None
    assert seen["chat_off"]["reasonCode"] == "chat_off" and seen["chat_off"]["switch"] == "off"
    assert seen["window_too_small"]["reasonCode"] == "window_too_small"
    outside = seen["outside_model"]
    assert outside["reasonCode"] == "outside_model" and outside["outside"] is not None
    assert set(outside["outside"]) == shape("ChatDocuments", "outside")
    assert set(outside["model"]) == shape("ChatDocuments", "model")
    assert set(outside["outside"]) == {"choice", "setBy", "canSendForThisChat", "sendForThisChat"}


def test_every_reason_a_chat_sends_nothing_is_one_the_page_has_words_for(world: World) -> None:
    """The reasons are read off the server's own source, so a new one is a failure here."""
    source = server_text("saved_api.py")
    said = set(re.findall(r'Access\(\s*False,\s*"(\w+)"', source))
    said |= set(re.findall(r'"(chat_off)"', source))
    assert said == union("DocumentsReason"), sorted(said ^ union("DocumentsReason"))


def test_the_owner_reading_a_chat_has_the_fields_the_page_reads_and_nothing_of_the_documents(
    world: World,
) -> None:
    ada, owner = start(world), start(world, "operator")
    keep(ada, ("survey.pdf", survey_pdf(docs.CODE_WORD)))
    chat = chat_on(ada)
    answer = ask(ada, chat, "What is the code word for the lighthouse inspection?")
    assert any(s.get("passage") for s in answer["sources"])
    settings(world, ownerReadsChats=True)
    try:
        view = owner.get(f"/api/chats/{chat}/documents").json()
        assert set(view) == interface("ChatDocumentsReadOnly")
        seen = owner.get(f"/api/chats/{chat}").json()["messages"][-1]
        hidden = [s for s in seen["sources"] if s.get("passage")]
        assert hidden and all(set(s) == interface("HiddenPassage") for s in hidden)
        assert set(seen["retrieval"]) == interface("SearchRecord")
    finally:
        settings(world, ownerReadsChats=None)


def test_a_chats_settings_are_the_ones_the_page_reads_and_writes() -> None:
    assert set(api.ChatSettings.model_fields) == interface("ChatSettings")


# --------------------------------------------------------------------------- #
# under an answer
# --------------------------------------------------------------------------- #


def test_a_passage_under_an_answer_has_the_fields_the_page_reads(world: World) -> None:
    ada = start(world)
    keep(ada, ("survey.pdf", survey_pdf(docs.CODE_WORD)))
    chat = chat_on(ada)
    world.gateway.words = ["The code word is ", docs.CODE_WORD, " [1]."]
    answer = ask(ada, chat, "What is the code word for the lighthouse inspection?")
    passages = [s for s in answer["sources"] if s.get("passage")]
    assert passages
    for passage in passages:
        # `cited` is added when the answer is done, which it is.
        assert set(passage) == interface("PassageSource") - {"wholeParts", "readBy"}
    assert passages[0]["cited"] is True and passages[0]["rank"] == 1
    assert passages[0]["page_start"] == 4 and passages[0]["via"] == "words"
    assert len(passages[0]["snippet"]) <= 240


def test_a_search_record_has_the_fields_the_page_reads_in_each_thing_it_can_say(
    world: World,
) -> None:
    ada = start(world)
    keep(ada, ("survey.pdf", survey_pdf(docs.CODE_WORD)))
    used = ask(ada, chat_on(ada), "What is the code word for the lighthouse inspection?")
    record = used["retrieval"]
    assert set(record) == interface("SearchRecord")
    assert record["status"] == "used"
    assert set(record["scope"]) == shape("SearchRecord", "scope")
    assert set(record["budget"]) == shape("SearchRecord", "budget")
    assert set(record["searched"]) == interface("DocumentCounts")
    nothing = ask(ada, chat_on(ada), "Tell me about the zebra giraffe okapi.")["retrieval"]
    assert nothing["status"] == "nothing_matched" and set(nothing) == interface("SearchRecord")
    strict = chat_on(ada, documentsOnly=True)
    own = ask(ada, strict, "Tell me about the quokka wombat numbat.")
    assert own["finish"] == "documents_only" and own["model"] is None
    assert set(own["retrieval"]) == interface("SearchRecord")
    assert own["retrieval"]["answeredBy"] == "workbench"


def test_every_status_a_search_can_come_to_is_one_the_page_has_words_for() -> None:
    """Read off the server's own source, so a new one is a failure here."""
    source = server_text("saved_api.py")
    said = set(re.findall(r'done\(\s*"(\w+)"', source))
    said |= set(re.findall(r'retrieval_view\(\s*"(\w+)"', source))
    said |= {"used"}
    assert said == union("SearchStatus"), sorted(said ^ union("SearchStatus"))


def test_what_the_page_says_while_an_answer_waits_is_what_the_server_sends() -> None:
    """The working line has words for each phase of the documents stage."""
    sent = set(re.findall(r'"stage": "documents", "phase": "(\w+)"', server_text("saved_api.py")))
    sent |= set(
        re.findall(r'"stage": "documents", "phase": "(\w+)"', server_text("documents_api.py"))
    )
    assert sent == {"searching", "reading"}
    phases = re.search(r"phase\?: ((?:\"\w+\"(?: \| )?\s*)+);", body("Progress"), re.DOTALL)
    assert phases and sent <= set(re.findall(r'"(\w+)"', phases.group(1)))


def test_a_passage_and_the_snippet_the_page_shows_agree_with_how_the_server_cuts_them() -> None:
    found = Retrieved(
        rank=1,
        doc_id="x",
        name="report.pdf",
        scope="library",
        kind="pdf",
        text="The code word is TAMARIND." * 40,
        heading="A > B",
        page_start=4,
        page_end=5,
        line_start=None,
        line_end=None,
        chunk_ids=(1, 2),
        tokens=300,
        via="words",
        terms=("code",),
    )
    entry = context.passage_entry(found)
    assert set(entry) == interface("PassageSource") - {"cited", "wholeParts", "readBy"}
    assert len(entry["snippet"]) <= 240 and entry["heading"] == "A > B"
