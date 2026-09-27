"""Upload validation tests: extension, magic bytes, size, filename, parsing."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.services.uploads import UploadError, parse_student_workbook, validate_upload


def make_upload_file(filename, content, content_type="application/octet-stream"):
    from fastapi import UploadFile
    return UploadFile(file=io.BytesIO(content), filename=filename,
                      headers={"content-type": content_type})


def xlsx_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["full_name", "father_name", "class_name", "section", "mobile"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.anyio
async def test_valid_xlsx_parses(settings):
    content = xlsx_bytes([["Ali Raza", "Muhammad Raza", "5", "A", "03001234567"]])
    up = await validate_upload(
        make_upload_file("students.xlsx", content,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        settings, allowed={"xlsx"})
    rows = parse_student_workbook(up)
    assert rows[0]["full_name"] == "Ali Raza"
    assert rows[0]["mobile"] == "03001234567"


@pytest.mark.anyio
async def test_rejects_bad_extension(settings):
    with pytest.raises(UploadError, match="not allowed"):
        await validate_upload(make_upload_file("evil.exe", b"MZ..."), settings)


@pytest.mark.anyio
async def test_rejects_mismatched_magic_bytes(settings):
    with pytest.raises(UploadError, match="signature"):
        await validate_upload(
            make_upload_file("fake.xlsx", b"definitely not a zip file" * 10), settings,
            allowed={"xlsx"})


@pytest.mark.anyio
async def test_rejects_oversize(settings):
    settings.upload_max_bytes = 100
    with pytest.raises(UploadError, match="exceeds"):
        await validate_upload(make_upload_file("big.xlsx", b"PK\x03\x04" + b"x" * 200),
                              settings, allowed={"xlsx"})


@pytest.mark.anyio
async def test_rejects_unsafe_filename(settings):
    for name in ["../secret.xlsx", ".hidden.xlsx", "a" * 200 + ".xlsx", ""]:
        with pytest.raises(UploadError, match=r"filename|not allowed"):
            await validate_upload(make_upload_file(name, b"PK\x03\x04data"), settings,
                                  allowed={"xlsx"})


@pytest.mark.anyio
async def test_rejects_empty_file(settings):
    with pytest.raises(UploadError, match="empty"):
        await validate_upload(make_upload_file("empty.xlsx", b""), settings,
                              allowed={"xlsx"})


@pytest.mark.anyio
async def test_rejects_mime_mismatch(settings):
    with pytest.raises(UploadError, match="MIME"):
        await validate_upload(
            make_upload_file("s.xlsx", b"PK\x03\x04data", "image/png"),
            settings, allowed={"xlsx"})


def test_parse_skips_blank_rows_and_requires_header(settings):
    content = xlsx_bytes([["", "", "", "", ""], ["Sara Khan", "", "6", "", ""]])
    up = type("U", (), {"filename": "s.xlsx", "extension": "xlsx",
                        "size": len(content), "content": content})()
    rows = parse_student_workbook(up)
    assert len(rows) == 1 and rows[0]["full_name"] == "Sara Khan"


@pytest.mark.anyio
async def test_rejects_empty_workbook(settings):
    empty = Workbook()
    buf = io.BytesIO()
    empty.save(buf)
    content = buf.getvalue()
    up = await validate_upload(
        make_upload_file(
            "empty.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        settings,
        allowed={"xlsx"},
    )
    with pytest.raises(UploadError, match="header|no student rows"):
        parse_student_workbook(up)


def test_upload_endpoint_roundtrip(client, auth_headers):
    content = xlsx_bytes([["Bilal Ahmed", "Ahmed", "4", "", "03001112222"]])
    r = client.post("/api/v1/uploads/students", headers=auth_headers,
                    files={"file": ("students.xlsx", content,
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1


def test_upload_endpoint_rejects_garbage(client, auth_headers):
    r = client.post("/api/v1/uploads/students", headers=auth_headers,
                    files={"file": ("x.xlsx", b"garbage",
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 400
