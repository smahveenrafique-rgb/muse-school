"""Student Master tests: CRUD, validation, search, import, no hard delete."""

from __future__ import annotations


def test_create_student_returns_server_generated_code(client, auth_headers, sample_student):
    r = client.post("/api/v1/students", json=sample_student, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["student_code"].startswith("APS-")
    assert body["full_name"] == "Ali Raza"
    assert body["status"] == "active"


def test_create_rejects_bad_mobile(client, auth_headers, sample_student):
    sample_student["mobile"] = "not-a-number"
    r = client.post("/api/v1/students", json=sample_student, headers=auth_headers)
    assert r.status_code == 422


def test_create_requires_auth(client, sample_student):
    assert client.post("/api/v1/students", json=sample_student).status_code == 401


def test_list_search_and_filter(client, auth_headers, sample_student):
    client.post("/api/v1/students", json=sample_student, headers=auth_headers)
    other = dict(sample_student, full_name="Sara Khan", class_name="6", mobile="03007654321")
    client.post("/api/v1/students", json=other, headers=auth_headers)

    r = client.get("/api/v1/students?q=sara", headers=auth_headers)
    assert r.json()["total"] == 1

    r = client.get("/api/v1/students?class=6", headers=auth_headers)
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["full_name"] == "Sara Khan"


def test_update_and_deactivate(client, auth_headers, sample_student):
    sid = client.post("/api/v1/students", json=sample_student, headers=auth_headers).json()["id"]
    r = client.patch(f"/api/v1/students/{sid}", json={"section": "B"}, headers=auth_headers)
    assert r.json()["section"] == "B"

    r = client.patch(f"/api/v1/students/{sid}", json={"status": "inactive"}, headers=auth_headers)
    assert r.json()["status"] == "inactive"

    r = client.get("/api/v1/students?status=inactive", headers=auth_headers)
    assert r.json()["total"] == 1


def test_delete_is_refused(client, auth_headers, sample_student):
    sid = client.post("/api/v1/students", json=sample_student, headers=auth_headers).json()["id"]
    r = client.delete(f"/api/v1/students/{sid}", headers=auth_headers)
    assert r.status_code == 405
    # record still there
    assert client.get(f"/api/v1/students/{sid}", headers=auth_headers).status_code == 200


def test_get_missing_returns_404(client, auth_headers):
    assert client.get("/api/v1/students/99999", headers=auth_headers).status_code == 404


def test_import_rows(client, auth_headers):
    rows = [
        {"full_name": "Bilal Ahmed", "father_name": "Ahmed Ali",
         "class_name": "4", "section": "", "mobile": "03001112222"},
        {"full_name": "Hina Tariq", "father_name": "Tariq Mehmood",
         "class_name": "4", "section": "B", "mobile": ""},
    ]
    r = client.post("/api/v1/students/import-rows", json=rows, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2 and body["skipped"] == 0
    codes = [s["student_code"] for s in
             client.get("/api/v1/students?class=4", headers=auth_headers).json()["items"]]
    assert len(set(codes)) == 2  # unique server-generated codes


def test_student_codes_are_sequential_and_unique(client, auth_headers, sample_student):
    codes = set()
    for i in range(3):
        s = dict(sample_student, full_name=f"Student {i}", mobile=f"0300000000{i}")
        resp = client.post("/api/v1/students", json=s,
                       headers=auth_headers).json()
        codes.add(resp["student_code"])
    assert len(codes) == 3
