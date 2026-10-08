"""The Speech and Transcription screens' API (workbench-media-screens.md §4,
slice 2). Real Workbench, fake Eugene and a fake gateway listing speech and
transcription models as the gateway does. Every test here fails against
Workbench before slice 2, whose media area had images alone.
"""

from __future__ import annotations

import time
from typing import Any

from eugene_plexus_workbench import files

from .conftest import MP3_CLIP, Browser, World, wav


def _wait(person: Browser, media_id: str, seconds: float = 15) -> dict[str, Any]:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        item = person.get(f"/api/media/{media_id}").json()
        if item["status"] != "running":
            return dict(item)
        time.sleep(0.05)
    raise AssertionError("the request did not finish")


def _speak(person: Browser, **fields: Any) -> dict[str, Any]:
    body = {"model": "openrouter/kokoro", "input": "The bench is ready.", "voice": "af_heart"}
    made = person.post("/api/media/speech", json={**body, **fields})
    assert made.status_code == 201, made.text
    return dict(made.json())


def _hear(person: Browser, data: bytes, name: str = "clip.wav", **fields: Any) -> dict[str, Any]:
    form = {"model": "openrouter/whisper-turbo", **{k: str(v) for k, v in fields.items()}}
    sent = person.post(
        "/api/media/transcription",
        data=form,
        files={"file": (name, data, "application/octet-stream")},
    )
    assert sent.status_code == 201, sent.text
    return dict(sent.json())


def test_the_screens_list_speech_and_transcription_models_with_what_they_take(
    world: World,
) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    doors = ada.get("/api/media/doors").json()["doors"]
    speech = {m["id"]: m for m in doors["speech"]["models"]}
    hearing = {m["id"]: m for m in doors["transcription"]["models"]}
    assert set(speech) == {"openrouter/kokoro", "local-voice", "eleven/eleven_flash_v2_5"}
    assert speech["openrouter/kokoro"]["voices"] == ["af_heart", "af_bella", "am_adam"]
    assert speech["openrouter/kokoro"]["voiceNames"] == {}
    # ElevenLabs' ids say nothing: their names ride beside them.
    assert speech["eleven/eleven_flash_v2_5"]["voiceNames"] == {
        "21m00Tcm4TlvDq8ikWAM": "Rachel",
        "EXAVITQu4vr4xnSDxMaL": "Sarah",
    }
    # pcm is left out: a browser cannot play raw samples.
    assert speech["openrouter/kokoro"]["formats"] == ["mp3", "wav"]
    assert speech["local-voice"]["voices"] is None and speech["local-voice"]["locality"] == "local"
    assert set(hearing) == {"openrouter/whisper-turbo", "oai/whisper-1"}
    assert hearing["oai/whisper-1"]["translates"] is True
    assert hearing["openrouter/whisper-turbo"]["translates"] is False


def test_a_clip_is_spoken_kept_and_played_back(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    item = _wait(ada, _speak(ada)["id"])
    assert item["status"] == "done", item
    assert world.gateway.speech_requests[-1] == {
        "model": "openrouter/kokoro",
        "input": "The bench is ready.",
        "voice": "af_heart",
        "response_format": "mp3",
    }
    [clip] = item["files"]
    assert clip["mediaType"] == "audio/mpeg" and clip["name"] == "speech.mp3"
    assert ada.get(f"/api/files/{clip['id']}").content == MP3_CLIP
    assert item["units"] == {"characters": 19}
    assert item["served"]["driver"] == "openrouter" and item["served"]["latency_ms"] == 420
    as_wav = _wait(ada, _speak(ada, format="wav")["id"])
    assert as_wav["files"][0]["mediaType"] == "audio/wav"


def test_a_refused_voice_is_the_gateways_words_with_its_field(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    words = "voice: 'openrouter/kokoro' has no voice 'nope'; it has af_heart. Nothing was sent."
    world.gateway.audio_refusal = (400, words, "voice")
    item = _wait(ada, _speak(ada, voice="nope")["id"])
    assert item["status"] == "failed" and item["files"] == []
    assert item["error"] == {"message": words, "param": "voice", "status": 400}


def test_a_recording_is_kept_and_sent_as_the_sdk_sends_it(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    clip = wav(0.5)
    item = _wait(ada, _hear(ada, clip, "note.wav", language="en", clipSeconds=3.07)["id"])
    assert item["status"] == "done", item
    assert item["text"] == "The bench is ready."
    door, fields, (name, sent, _) = world.gateway.transcriptions[-1]
    assert door == "transcriptions"
    assert fields == {
        "model": "openrouter/whisper-turbo",
        "response_format": "json",
        "language": "en",
    }
    assert (name, sent) == ("note.wav", clip)
    # The audio stays in the bin beside the text, to play again.
    [kept] = item["files"]
    assert kept["mediaType"] == "audio/wav" and ada.get(f"/api/files/{kept['id']}").content == clip
    assert item["units"] == {"heardSeconds": 3.0, "tokens": None, "clipSeconds": 3.07}


def test_what_the_model_heard_is_kept_beside_the_clips_length(world: World) -> None:
    """Measured 2026-10-08: OpenRouter's whisper heard 1.5 s of a 3.1 s MP3,
    every time. The page sets the two side by side; here they are kept."""
    ada = world.browser()
    ada.sign_in("p-ada")
    world.gateway.heard_seconds = 1.525
    item = _wait(ada, _hear(ada, wav(0.5), clipSeconds=3.07)["id"])
    assert (item["units"]["heardSeconds"], item["units"]["clipSeconds"]) == (1.525, 3.07)
    world.gateway.heard_seconds = None
    tokens = _wait(ada, _hear(ada, wav(0.5))["id"])
    assert tokens["units"] == {"heardSeconds": None, "tokens": 41, "clipSeconds": None}


def test_translate_goes_to_the_translations_door_without_a_language(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    item = _wait(
        ada, _hear(ada, wav(0.5), model="oai/whisper-1", translate="true", language="fr")["id"]
    )
    assert item["status"] == "done"
    door, fields, _ = world.gateway.transcriptions[-1]
    assert door == "translations" and "language" not in fields
    assert item["request"]["translate"] is True


def test_audio_that_is_not_audio_is_refused_before_anything_is_sent(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    refused = ada.post(
        "/api/media/transcription",
        data={"model": "openrouter/whisper-turbo"},
        files={"file": ("notes.txt", b"plain words", "text/plain")},
    )
    assert refused.status_code == 415 and "MP3, WAV" in refused.text
    assert world.gateway.transcriptions == []


def test_every_recording_kind_measured_is_taken(world: World) -> None:
    """Chrome records WebM/Opus, a phone MP4/AAC; speech models make Ogg,
    FLAC and AAC too. Each is recognised by its bytes."""
    samples = {
        "audio/webm": b"\x1a\x45\xdf\xa3" + bytes(40),
        "audio/ogg": b"OggS" + bytes(40),
        "audio/flac": b"fLaC" + bytes(40),
        "audio/mp4": b"\x00\x00\x00\x20ftypM4A " + bytes(40),
        "audio/aac": b"\xff\xf1\x50\x80" + bytes(40),
        "audio/mpeg": MP3_CLIP,
        "audio/wav": wav(0.1),
    }
    for kind, data in samples.items():
        assert files.sniff_audio(data) == kind, kind
    assert files.sniff_audio(b"\xff\xd8\xff\xe0") is None, "a JPEG is not audio"


def test_send_to_a_chat_takes_mp3_and_wav_and_says_why_not_the_rest(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    item = _wait(ada, _speak(ada)["id"])
    sent = ada.post(f"/api/media/{item['id']}/to-chat", json={"fileId": item["files"][0]["id"]})
    assert sent.status_code == 201, sent.text
    assert sent.json()["attachment"]["mediaType"] == "audio/mpeg"
    assert ada.get(f"/api/chats/{sent.json()['chatId']}").json()["chat"]["title"] == (
        "The bench is ready."
    )
    hearing = _wait(ada, _hear(ada, b"OggS" + bytes(80), "note.ogg")["id"])
    refused = ada.post(
        f"/api/media/{hearing['id']}/to-chat", json={"fileId": hearing["files"][0]["id"]}
    )
    assert refused.status_code == 400 and "WAV and MP3 audio" in refused.text
    assert "OGG" in refused.text


def test_each_screen_lists_its_own_results(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    spoken = _wait(ada, _speak(ada)["id"])
    heard = _wait(ada, _hear(ada, wav(0.2))["id"])
    by_door = {
        door: [i["id"] for i in ada.get("/api/media", params={"door": door}).json()["items"]]
        for door in ("images", "speech", "transcription")
    }
    assert by_door == {"images": [], "speech": [spoken["id"]], "transcription": [heard["id"]]}
    assert ada.delete("/api/media", params={"door": "speech"}).status_code == 204
    assert ada.get("/api/media", params={"door": "transcription"}).json()["items"]
