import importlib, os
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_DB", str(tmp_path / "test.sqlite3"))
    import app.main as m

    importlib.reload(m)
    m.create_user("alice", "password-one")
    m.create_user("bob", "password-two")
    a = TestClient(m.app)
    b = TestClient(m.app)
    for c, u, p in [(a, "alice", "password-one"), (b, "bob", "password-two")]:
        r = c.post("/api/login", json={"username": u, "password": p})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return m, a, b


def test_security_and_isolation(env):
    m, a, b = env
    assert (
        a.post(
            "/api/projects",
            json={"name": "A"},
            headers={"Origin": "https://evil.invalid"},
        ).status_code
        == 403
    )
    token = a.headers.pop("X-CSRF-Token")
    assert a.post("/api/tasks", json={"title": "x"}).status_code == 403
    a.headers["X-CSRF-Token"] = token
    t = a.post("/api/tasks", json={"title": "secret"}).json()
    id = t["id"]
    assert b.get("/api/tasks/" + id).status_code == 404
    assert b.put("/api/tasks/" + id, json={"title": "stolen"}).status_code == 404
    assert (
        b.post(
            "/api/tasks/bulk-due", json={"ids": [id], "due_date": "2026-01-01"}
        ).status_code
        == 404
    )
    assert a.get("/api/tasks/" + id).json()["due_date"] is None
    assert a.post("/api/logout", json={}).status_code == 200
    assert a.get("/api/me").status_code == 401


def test_tree_and_atomicity(env):
    m, a, b = env
    p = a.post("/api/projects", json={"name": "P"}).json()
    q = a.post("/api/projects", json={"name": "Q"}).json()
    t = a.post("/api/tasks", json={"title": "root", "project_id": p["id"]}).json()
    c = a.post(
        "/api/tasks",
        json={"title": "child", "parent_id": t["id"], "project_id": p["id"]},
    ).json()
    assert (
        a.put("/api/tasks/" + t["id"], json={**t, "parent_id": c["id"]}).status_code
        == 400
    )
    assert (
        a.put("/api/tasks/" + t["id"], json={**t, "project_id": q["id"]}).status_code
        == 400
    )
    assert (
        a.put("/api/tasks/" + t["id"], json={**t, "status": "done"}).status_code == 200
    )
    assert a.get("/api/tasks/" + c["id"]).json()["status"] == "waiting"
    duplicate = a.post("/api/tasks/" + t["id"] + "/duplicate", json={}).json()
    assert duplicate["status"] == "waiting"
    assert len(a.get("/api/tasks/" + duplicate["id"] + "/delete-preview").json()) == 1
    old = a.get("/api/tasks/" + t["id"] + "/delete-preview").json()
    newer = a.post(
        "/api/tasks", json={"title": "new", "parent_id": t["id"], "project_id": p["id"]}
    ).json()
    assert (
        a.post(
            "/api/tasks/" + t["id"] + "/delete", json={"ids": [x["id"] for x in old]}
        ).status_code
        == 409
    )
    assert (
        a.post(
            "/api/tasks/bulk-due",
            json={"ids": [t["id"], "unknown"], "due_date": "2026-01-01"},
        ).status_code
        == 404
    )
    assert a.get("/api/tasks/" + t["id"]).json()["due_date"] is None
    assert (
        a.post(
            "/api/tasks/bulk-due", json={"ids": [t["id"]], "due_date": "2026-01-01"}
        ).status_code
        == 200
    )
    assert a.get("/api/tasks/" + c["id"]).json()["due_date"] is None
    assert (
        a.post(
            "/api/tasks/" + t["id"] + "/delete",
            json={"ids": [x["id"] for x in old] + [newer["id"]]},
        ).status_code
        == 200
    )
    assert a.get("/api/tasks/" + c["id"]).status_code == 404


def test_captcha_and_markdown(env):
    m, a, b = env
    c = TestClient(m.app)
    for _ in range(3):
        assert (
            c.post(
                "/api/login", json={"username": " alice ", "password": "bad"}
            ).status_code
            == 401
        )
    challenge = c.get("/api/captcha", params={"username": "alice"}).json()
    assert challenge["required"]
    assert (
        c.post(
            "/api/login",
            json={"username": "alice", "password": "password-one", "captcha": "bad"},
        ).status_code
        == 400
    )
    challenge = c.get("/api/captcha", params={"username": "alice"}).json()
    x, y = [int(x) for x in challenge["question"].split() if x.isdigit()]
    assert (
        c.post(
            "/api/login",
            json={
                "username": "alice",
                "password": "password-one",
                "captcha": str(x + y),
            },
        ).status_code
        == 200
    )
    html = a.post(
        "/api/markdown",
        json={"text": "**bold** <script>alert(1)</script> [x](javascript:alert(1))"},
    ).json()["html"]
    assert (
        "<strong>bold</strong>" in html
        and "<script>" not in html
        and 'href="javascript:' not in html
    )


def test_deep_tree_deletion(env):
    m, a, b = env
    owner = 1
    with m.connect() as c:
        for i in range(1101):
            c.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"deep-{i}",
                    owner,
                    f"task {i}",
                    "waiting",
                    "",
                    "text",
                    f"deep-{i-1}" if i else None,
                    None,
                    "[]",
                    None,
                    m.now(),
                ),
            )
    preview = a.get("/api/tasks/deep-0/delete-preview")
    assert len(preview.json()) == 1101
    result = a.post(
        "/api/tasks/deep-0/delete", json={"ids": [x["id"] for x in preview.json()]}
    )
    assert result.status_code == 200
    assert a.get("/api/tasks/deep-1100").status_code == 404


def test_login_ip_limit_and_creation_validation(env):
    m, a, b = env
    with pytest.raises(ValueError):
        m.create_user("x" * 201, "long-enough-password")
    with pytest.raises(ValueError):
        m.create_user("valid", "x" * 1001)
    c = TestClient(m.app)
    for i in range(30):
        assert (
            c.post(
                "/api/login", json={"username": f"unknown-{i}", "password": "bad"}
            ).status_code
            == 401
        )
    assert (
        c.post("/api/login", json={"username": "other", "password": "bad"}).status_code
        == 429
    )


def test_history_labels_and_parent_links(env):
    m, a, b = env
    p = a.post("/api/projects", json={"name": "P", "description": "D"}).json()
    assert (
        a.put(
            "/api/projects/" + p["id"], json={"name": "P2", "color": "#abcdef"}
        ).json()["name"]
        == "P2"
    )
    parent = a.post(
        "/api/tasks", json={"title": "parent", "project_id": p["id"]}
    ).json()
    child = a.post(
        "/api/tasks",
        json={
            "title": "child",
            "project_id": p["id"],
            "parent_id": parent["id"],
            "labels": ["tag", "tag"],
        },
    ).json()
    assert child["labels"] == ["tag"]
    assert (
        a.post(
            "/api/tasks", json={"title": "bad", "parent_id": parent["id"]}
        ).status_code
        == 400
    )
    assert (
        a.put(
            "/api/tasks/" + child["id"],
            json={**child, "status": "done", "due_date": "2026-12-01"},
        ).status_code
        == 200
    )
    events = a.get("/api/tasks/" + child["id"]).json()["history"]
    assert {"created", "status_changed", "due_changed"} <= {e["type"] for e in events}
    assert "subtask_done" in {
        e["type"] for e in a.get("/api/tasks/" + parent["id"]).json()["history"]
    }


def test_schema_dictionary_covers_every_column(env):
    import json
    from pathlib import Path

    m, _, _ = env
    descriptions = json.loads(
        Path("app/schema-descriptions.json").read_text(encoding="utf-8")
    )
    with m.connect() as c:
        tables = {
            r["name"]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert set(descriptions) == tables
        for table in tables:
            columns = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            assert set(descriptions[table]) == columns
            assert all(len(v) > 15 for v in descriptions[table].values())
