"""Page previews enforce the same person boundary as original downloads."""

from . import documents
from .conftest import World
from .saved import keep, start


def test_page_png_marks_passage(world: World) -> None:
    ada = start(world)
    doc = keep(ada, ("report.pdf", documents.three_pages()))["documents"][0]
    found = ada.post("/api/documents/test", json={"question": "TAMARIND-6042"})
    assert found.status_code == 200, found.text
    chunk = found.json()["passages"][0]["chunkIds"][0]
    path = f"/api/documents/{doc['id']}/pages/3"
    response = ada.get(path, params={"passage": chunk})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["image"].startswith("data:image/png;base64,")
    assert page["pageCount"] == 3 and page["boxes"]
    assert all(
        0 <= x < page["width"] and 0 <= y < page["height"] and w > 0 and h > 0
        for x, y, w, h in page["boxes"]
    )
    png = ada.get(path + ".png")
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    bo = start(world, "p-bo")
    assert bo.get(path, params={"passage": chunk}).status_code == 404
    assert bo.get(path + ".png").status_code == 404
