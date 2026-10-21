"""Per-chat settings that follow the chat between browsers."""

from .conftest import World


def test_run_artifacts_off_is_kept(world: World) -> None:
    ada = world.browser()
    ada.sign_in("p-ada")
    chat = ada.new_chat()
    path = f"/api/chats/{chat}"
    assert ada.get(path).json()["chat"]["settings"]["runArtifacts"] is None

    saved = ada.patch(path, json={"settings": {"runArtifacts": False}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["runArtifacts"] is False

    other = world.browser()
    other.sign_in("p-ada")
    assert other.get(path).json()["chat"]["settings"]["runArtifacts"] is False
    # An unrelated settings change must preserve an explicit False.
    other.patch(path, json={"settings": {"temperature": 0.2}})
    assert ada.get(path).json()["chat"]["settings"]["runArtifacts"] is False

    for value in (True, None):
        restored = other.patch(path, json={"settings": {"runArtifacts": value}})
        assert restored.status_code == 200, restored.text
        assert ada.get(path).json()["chat"]["settings"]["runArtifacts"] is value
