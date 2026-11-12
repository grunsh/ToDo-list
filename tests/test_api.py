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


def test_precise_history_and_legacy_projection(env):
    m, a, _ = env
    t = a.post("/api/tasks", json={"title": "one"}).json()
    url = "/api/tasks/" + t["id"]
    assert a.put(url, json=t).status_code == 200
    assert [h["type"] for h in a.get(url).json()["history"]] == ["created"]
    t = a.put(
        url,
        json={
            **t,
            "status": "in_progress",
            "due_date": "2026-12-01",
            "title": "two",
            "labels": ["x"],
        },
    ).json()
    hist = a.get(url).json()["history"]
    assert [h["type"] for h in hist] == [
        "edited",
        "due_changed",
        "status_changed",
        "created",
    ]
    assert set(hist[0]["data"]["fields"]) == {"title", "labels"}
    assert (
        a.post(
            "/api/tasks/bulk-due", json={"ids": [t["id"]], "due_date": "2026-12-01"}
        ).status_code
        == 200
    )
    assert len(a.get(url).json()["history"]) == 4
    with m.connect() as c:
        c.execute(
            "INSERT INTO history(owner_id,task_id,at,type,data) VALUES(?,?,?,?,?)",
            (1, t["id"], "2026-01-01T00:00:00+00:00", "status_changed", "{}"),
        )
        c.execute(
            "INSERT INTO history(owner_id,task_id,at,type,data) VALUES(?,?,?,?,?)",
            (1, t["id"], "2026-01-01T00:00:00.5+00:00", "updated", "{}"),
        )
        c.execute(
            "INSERT INTO history(owner_id,task_id,at,type,data) VALUES(?,?,?,?,?)",
            (1, t["id"], "2026-01-01T00:00:03+00:00", "updated", "{}"),
        )
    hist = a.get(url).json()["history"]
    assert [h["type"] for h in hist[:2]] == ["edited", "status_changed"]
    with m.connect() as c:
        assert (
            c.execute("SELECT count(*) FROM history WHERE type='updated'").fetchone()[0]
            == 2
        )


def test_project_markdown_labels_and_recent_colors(env):
    m, a, b = env
    p = a.post(
        "/api/projects",
        json={
            "name": "P",
            "description": "**bold** <script>x</script> [x](javascript:alert(1))",
            "description_format": "markdown",
            "labels": ["a", "b"],
            "color": "#abcdef",
        },
    )
    assert p.status_code == 200
    data = p.json()
    assert (
        data["color"] == "#ABCDEF"
        and data["labels"] == ["a", "b"]
        and "<strong>bold</strong>" in data["description_html"]
        and "<script>" not in data["description_html"]
        and 'href="javascript:' not in data["description_html"]
    )
    assert a.get("/api/projects").json()[0]["labels"] == ["a", "b"]
    assert b.get("/api/project-colors").json() == []
    for i in range(22):
        assert (
            a.put(
                "/api/projects/" + data["id"], json={"name": "P", "color": f"#{i:06x}"}
            ).status_code
            == 200
        )
    colors = a.get("/api/project-colors").json()
    assert len(colors) == 20 and colors[0] == "#000015" and "#000000" not in colors
    assert (
        a.put(
            "/api/projects/" + data["id"], json={"name": "P", "color": "#000005"}
        ).status_code
        == 200
    )
    colors = a.get("/api/project-colors").json()
    assert colors[0] == "#000005" and colors.count("#000005") == 1
    assert b.put("/api/projects/" + data["id"], json={"name": "P"}).status_code == 404
    assert (
        a.post("/api/projects", json={"name": "bad", "color": "red"}).status_code == 422
    )
    assert (
        a.post(
            "/api/projects", json={"name": "bad", "description_format": "html"}
        ).status_code
        == 422
    )
    assert (
        a.post("/api/projects", json={"name": "bad", "labels": ["x"] * 51}).status_code
        == 422
    )


def test_legacy_project_migration_preserves_data(tmp_path, monkeypatch):
    import sqlite3, importlib

    path = tmp_path / "legacy.sqlite3"
    c = sqlite3.connect(path)
    c.executescript(
        """CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE projects(id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, description TEXT NOT NULL, color TEXT NOT NULL, created_at TEXT NOT NULL);
    INSERT INTO users VALUES(1,'old','hash','2026'); INSERT INTO projects VALUES('p',1,'Old','text','#123456','2026');"""
    )
    c.commit()
    c.close()
    monkeypatch.setenv("TODO_DB", str(path))
    import app.main as m

    importlib.reload(m)
    m.init()
    with m.connect() as c:
        project = dict(c.execute("SELECT * FROM projects WHERE id='p'").fetchone())
        assert (
            project["name"] == "Old"
            and project["description"] == "text"
            and project["description_format"] == "text"
            and project["labels"] == "[]"
        )
