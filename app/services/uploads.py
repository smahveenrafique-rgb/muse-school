"""Upload validation — extension, MIME, size, filename, parse result.

Nothing is trusted: the file is sniffed (magic bytes), size-capped before
reading, and parsed defensively. Only the normalised rows leave this module.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from fastapi import UploadFile

from app.config import Settings

# Magic-byte signatures we accept.
_SIGNATURES = {
    "xlsx": [b"PK\x03\x04"],                       # zip container
    "xls": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],   # OLE2
    "pdf": [b"%PDF"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
}

_MIME_BY_EXT = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}

_SAFE_NAME_RE = re.compile(r"^[\w\-. ]{1,120}$")


@dataclass
class ValidatedUpload:
    filename: str
    extension: str
    size: int
    content: bytes


class UploadError(ValueError):
    pass


async def validate_upload(file: UploadFile, settings: Settings,
                          allowed: set[str] | None = None) -> ValidatedUpload:
    allowed = allowed or settings.upload_allowed_extension_set
    filename = (file.filename or "").strip()
    if not _SAFE_NAME_RE.match(filename) or ".." in filename or filename.startswith("."):
        raise UploadError("unsafe filename")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in allowed:
        raise UploadError(f"extension .{ext} not allowed")
    # Size-cap BEFORE full read.
    chunks, total = [], 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > settings.upload_max_bytes:
            raise UploadError(f"file exceeds {settings.upload_max_bytes} bytes")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise UploadError("empty file")
    sigs = _SIGNATURES.get(ext, [])
    if sigs and not any(content.startswith(s) for s in sigs):
        raise UploadError(f"content does not match .{ext} signature")
    declared = (file.content_type or "").split(";")[0].strip().lower()
    expected = _MIME_BY_EXT.get(ext, "")
    if (declared and expected and declared != expected
            and not declared.startswith("application/octet-stream")):
        raise UploadError(f"MIME {declared} does not match .{ext}")
    return ValidatedUpload(filename=filename, extension=ext, size=total, content=content)


def parse_student_workbook(upload: ValidatedUpload) -> list[dict]:
    """Normalise an xlsx/xls/csv student list to StudentImportRow dicts.

    Expected header (case-insensitive): full_name | father_name | class_name
    | section | mobile. Unknown columns are ignored; missing optional
    columns default to "".
    """
    rows: list[dict] = []
    if upload.extension == "csv":
        text = upload.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        raw_rows = list(reader)
    else:
        try:
            from openpyxl import load_workbook  # lazy: only needed for xlsx
        except ImportError as exc:
            raise UploadError("xlsx support requires openpyxl") from exc
        wb = load_workbook(io.BytesIO(upload.content), read_only=True, data_only=True)
        ws = wb.active
        try:
            header_row = next(ws.iter_rows(values_only=True))
        except StopIteration as exc:
            raise UploadError("empty workbook: missing header row") from exc
        header_cells = header_row
        headers = [str(h).strip().lower() if h is not None else "" for h in header_cells]
        raw_rows = [dict(zip(headers, row, strict=False))
                    for row in ws.iter_rows(min_row=2, values_only=True)]
    def get(r: dict, *names: str) -> str:
        for n in names:
            if n in r and r[n] not in (None, ""):
                return str(r[n]).strip()
        return ""
    for _lineno, r in enumerate(raw_rows, start=2):
        name = get(r, "full_name", "name", "student_name")
        if not name:
            continue  # skip blank rows
        rows.append({
            "full_name": name,
            "father_name": get(r, "father_name", "father", "guardian"),
            "class_name": get(r, "class_name", "class", "grade"),
            "section": get(r, "section", "sec"),
            "mobile": get(r, "mobile", "phone", "whatsapp"),
        })
    if not rows:
        raise UploadError("no student rows found — check the header row")
    return rows
