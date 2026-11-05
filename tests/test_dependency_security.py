"""Security floors and offline compatibility checks for patched dependencies."""

from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


SECURITY_FLOORS = {
    "pillow": "13.0.0",
    "python-multipart": "0.0.32",
    "certifi": "2026.10.18",
    "httpx2": "2.12.0",
    "httpcore2": "2.12.0",
    "protobuf": "6.33.5",
}


@pytest.mark.parametrize("package,floor", SECURITY_FLOORS.items())
def test_security_floors_declared_and_installed(package, floor):
    text = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
    requirements = [
        Requirement(line.split("#", 1)[0].strip())
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    matches = [r for r in requirements if canonicalize_name(r.name) == package]
    assert len(matches) == 1, f"Declare {package} exactly once"
    assert any(
        s.operator in (">=", "==") and Version(s.version) >= Version(floor)
        for s in matches[0].specifier
    ), f"{package} must require a patched release"
    assert Version(version(package)) >= Version(floor)
    assert matches[0].specifier.contains(version(package))


def test_patched_pillow_embeds_image_in_excel():
    from PIL import Image
    from openpyxl import Workbook, load_workbook
    from openpyxl.drawing.image import Image as ExcelImage

    png = BytesIO()
    Image.new("RGB", (16, 16), "purple").save(png, format="PNG")
    png.seek(0)
    workbook = Workbook()
    workbook.active.add_image(ExcelImage(png), "C2")
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    restored = load_workbook(output)
    assert len(restored.active._images) == 1
    assert restored.active._images[0].width == 16


def test_patched_multipart_and_http_client_accept_upload():
    from fastapi import FastAPI, File, Form, UploadFile
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/upload")
    async def upload(label: str = Form(...), file: UploadFile = File(...)):
        return {"label": label, "filename": file.filename, "size": len(await file.read())}

    with TestClient(app) as client:
        response = client.post(
            "/upload", data={"label": "proposal"},
            files={"file": ("proposal.txt", b"offline test", "text/plain")},
        )
    assert response.status_code == 200
    assert response.json() == {
        "label": "proposal", "filename": "proposal.txt", "size": 12,
    }
