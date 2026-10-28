from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from teamsync.config import Settings
from teamsync.web import create_app

ADMIN_PW = "admin-password-1"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", admin_password=ADMIN_PW, watch_dir=None, _env_file=None)
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    r = client.post(
        "/login", data={"username": "admin", "password": ADMIN_PW, "next": "/"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    return client


@pytest.fixture
def token(admin: TestClient) -> str:
    r = admin.post("/admin/tokens", data={"name": "test-box"}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].split("token=")[1]


def upload(client: TestClient, path: Path, token: str, **fields: str) -> dict:
    with path.open("rb") as fh:
        r = client.post(
            "/api/upload",
            files={"file": (path.name, fh, "application/octet-stream")},
            data=fields,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    return r.json()


# --- auth and pages --------------------------------------------------------


def test_health_is_public(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_pages_redirect_to_login(client: TestClient) -> None:
    r = client.get("/entries?flag=any", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/entries?flag=any"


def test_login_rejects_bad_password(client: TestClient) -> None:
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 200 and "Invalid username or password" in r.text


def test_login_ignores_offsite_next(client: TestClient) -> None:
    r = client.post(
        "/login",
        data={"username": "admin", "password": ADMIN_PW, "next": "https://evil.example"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


def test_dashboard_and_logout(admin: TestClient) -> None:
    r = admin.get("/")
    assert r.status_code == 200 and "Dashboard" in r.text and "admin · admin" in r.text
    r = admin.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert admin.get("/", follow_redirects=False).status_code == 303


def test_viewer_cannot_reach_admin(admin: TestClient) -> None:
    admin.post("/admin/users", data={"username": "vic", "password": "viewer-password", "role": "viewer"})
    viewer = TestClient(admin.app)
    viewer.post("/login", data={"username": "vic", "password": "viewer-password"})
    assert viewer.get("/entries").status_code == 200
    assert viewer.get("/admin").status_code == 403
    assert viewer.post("/properties", data={"canonical_name": "X"}).status_code == 403
    assert viewer.post("/api/reparse").status_code == 403
    assert viewer.get("/api/summary").status_code == 200


# --- API -------------------------------------------------------------------


def test_api_requires_auth(client: TestClient) -> None:
    assert client.get("/api/summary").status_code == 401
    assert client.get("/api/summary", headers={"Authorization": "Bearer tsk_bad_bad"}).status_code == 401


def test_known_hashes_validates_input(client: TestClient, token: str) -> None:
    r = client.get("/api/files/known?hashes=zz", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_upload_flow(client: TestClient, token: str, fixtures: Path) -> None:
    src = fixtures / "Sample Hotel JE_100001_200001.CSV"
    payload = upload(client, src, token, source_path="Batch A/" + src.name, batch_folder="Batch A")
    assert payload["new"] == 1 and payload["parsed"] == 1
    entry_id = payload["files"][0]["entry_id"]

    # same bytes again -> duplicate, nothing new
    again = upload(client, src, token)
    assert again["duplicate"] == 1 and again["new"] == 0

    import hashlib

    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    r = client.get(
        f"/api/files/known?hashes={digest},{'0' * 64}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.json() == {"known": [digest], "unknown": ["0" * 64]}

    r = client.get(f"/api/entries/{entry_id}", headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    assert body["entry"]["batch_folder"] == "Batch A"
    assert body["entry"]["validation_flags"] == [] and body["entry"]["is_balanced"] is True
    assert len(body["lines"]) == 10

    r = client.get("/api/entries?property=sample", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["total"] == 1

    r = client.get(
        f"/api/files/{body['entry']['source_file_id']}/raw", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200 and r.content == src.read_bytes()


def test_upload_rejects_empty_and_unsupported_is_stored(
    client: TestClient, token: str, fixtures: Path
) -> None:
    r = client.post(
        "/api/upload",
        files={"file": ("x.csv", b"", "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    payload = upload(client, fixtures / "notes.txt", token)
    assert payload["files"][0]["parse_status"] == "unsupported"


def test_sync_without_watch_dir_is_409(client: TestClient, token: str) -> None:
    assert client.post("/api/sync", headers={"Authorization": f"Bearer {token}"}).status_code == 409


def test_export_csv(client: TestClient, token: str, fixtures: Path) -> None:
    upload(client, fixtures / "Multi Store JE_100003_200003.CSV", token)
    r = client.get("/api/export/entries.csv", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 6
    assert rows[0]["account_ref"] == "Payroll Clearing" and rows[0]["credit"] == "3000.00"
    assert "multi_subsidiary" in rows[0]["validation_flags"]


# --- admin pages -----------------------------------------------------------


def test_admin_upload_reparse_and_pages(admin: TestClient, fixtures: Path) -> None:
    files = [
        ("files", (p.name, p.read_bytes(), "application/octet-stream"))
        for p in (fixtures / "Shifted Inn JE_100002_200002.CSV", fixtures / "random-report.csv")
    ]
    r = admin.post("/admin/upload", files=files, follow_redirects=False)
    assert r.status_code == 303 and "2+new" in r.headers["location"]

    assert "suspect_column_shift" in admin.get("/").text
    assert "Shifted Inn" in admin.get("/entries?flag=suspect_column_shift").text
    assert "No entries match" in admin.get("/entries?flag=unbalanced").text
    assert "unrecognized_layout" in admin.get("/files").text
    assert admin.get("/files?status=parsed").status_code == 200

    entry_page = admin.get("/entries/1")
    assert entry_page.status_code == 200 and "Shifted Inn" in entry_page.text
    assert admin.get("/entries/999").status_code == 404
    assert admin.get("/files/1/download").status_code == 200

    r = admin.post("/admin/reparse", follow_redirects=False)
    assert "Reparse+run" in r.headers["location"]
    admin_page = admin.get("/admin")
    assert "reparse" in admin_page.text and "upload" in admin_page.text


def test_admin_user_and_token_management(admin: TestClient) -> None:
    r = admin.post(
        "/admin/users",
        data={"username": "sam", "password": "short", "role": "viewer"},
        follow_redirects=False,
    )
    assert "err=" in r.headers["location"]
    r = admin.post(
        "/admin/users",
        data={"username": "sam", "password": "sam-password-1", "role": "viewer"},
        follow_redirects=False,
    )
    assert "msg=" in r.headers["location"]
    page = admin.get("/admin").text
    assert "sam" in page
    r = admin.post("/admin/users/1/delete", follow_redirects=False)
    assert "cannot+delete+yourself" in r.headers["location"]

    r = admin.post("/admin/tokens", data={"name": "ws"}, follow_redirects=False)
    plain = r.headers["location"].split("token=")[1]
    assert plain.startswith("tsk_")
    assert plain in admin.get(r.headers["location"]).text
    r = admin.post("/admin/tokens/1/revoke", follow_redirects=False)
    assert "revoked" in r.headers["location"]
    assert admin.get("/api/summary", headers={"Authorization": f"Bearer {plain}"}).status_code == 401


def test_property_mapping(admin: TestClient, fixtures: Path) -> None:
    p = fixtures / "Mixed Lodge JE_100004_200004.CSV"
    admin.post("/admin/upload", files=[("files", (p.name, p.read_bytes(), "application/octet-stream"))])
    page = admin.get("/properties").text
    assert "Mixed Lodge" in page and "unmapped" in page

    r = admin.post(
        "/properties/alias",
        data={"alias": "Mixed Lodge", "new_name": "Mixed Lodge Hotel"},
        follow_redirects=False,
    )
    assert "msg=" in r.headers["location"]
    page = admin.get("/properties").text
    assert "Mixed Lodge Hotel" in page and "unmapped" not in page

    # entries list and search see the canonical name
    assert "Mixed Lodge Hotel" in admin.get("/entries?q=hotel").text
    assert "Mixed Lodge Hotel" in admin.get("/entries/1").text

    r = admin.post("/properties", data={"canonical_name": "Mixed Lodge Hotel"}, follow_redirects=False)
    assert "already+exists" in r.headers["location"]
    r = admin.post("/properties/alias/delete", data={"alias": "Mixed Lodge"}, follow_redirects=False)
    assert "Unmapped" in r.headers["location"]
    r = admin.post("/properties/1/delete", follow_redirects=False)
    assert "deleted" in r.headers["location"]
    assert "unmapped" in admin.get("/properties").text


def test_watch_dir_sync(tmp_path: Path, fixtures: Path) -> None:
    inbox = tmp_path / "inbox" / "Batch 1"
    inbox.mkdir(parents=True)
    src = fixtures / "Sample Hotel JE_100001_200001.ods"
    (inbox / src.name).write_bytes(src.read_bytes())
    settings = Settings(
        data_dir=tmp_path / "data",
        admin_password=ADMIN_PW,
        watch_dir=tmp_path / "inbox",
        sync_interval_seconds=0,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as c:
        c.post("/login", data={"username": "admin", "password": ADMIN_PW})
        r = c.post("/api/sync")
        assert r.status_code == 200 and r.json()["parsed"] == 1
        assert r.json()["files"][0]["name"] == src.name
        r = c.post("/admin/sync", follow_redirects=False)
        assert "0+new" in r.headers["location"]
        assert "Batch 1" in c.get("/entries").text


def test_generated_admin_password_is_written(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", admin_password=None, _env_file=None)
    with TestClient(create_app(settings)) as c:
        marker = tmp_path / "data" / "initial-admin-password.txt"
        assert marker.is_file()
        username, password = marker.read_text().split()
        r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
