import os, sqlite3, hashlib, secrets, hmac, time, json, re
from pathlib import Path
from contextlib import contextmanager
from threading import RLock
import markdown, bleach, html
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DB = os.environ.get("TODO_DB", "data/todo.sqlite3")
app = FastAPI()
app.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
)


# SQLite использует отдельное соединение на запрос; внешние ключи включаются явно.
@contextmanager
def connect():
    """Транзакция фиксируется только при успехе; соединение закрывается всегда."""
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=10000")
    try:
        with c:
            yield c
    finally:
        c.close()


def init():
    """Создаём постоянную схему при первом старте; данные не пересоздаются."""
    with connect() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, description TEXT NOT NULL, color TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), title TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('waiting','in_progress','done')), description TEXT NOT NULL, description_format TEXT NOT NULL CHECK(description_format IN ('text','markdown')), parent_id TEXT REFERENCES tasks(id) ON DELETE CASCADE, project_id TEXT REFERENCES projects(id), labels TEXT NOT NULL, due_date TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, at TEXT NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(owner_id); CREATE INDEX IF NOT EXISTS history_task ON history(owner_id,task_id);
""")


init()


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password, salt=None):
    """Scrypt с индивидуальной случайной солью; в строке хранится соль и хеш."""
    salt = salt or secrets.token_hex(16)
    return (
        salt
        + ":"
        + hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
        ).hex()
    )


def create_user(username, password):
    if (
        not username.strip()
        or len(username.strip()) > 200
        or not 8 <= len(password) <= 1000
    ):
        raise ValueError("Логин: от 1 до 200 символов; пароль: от 8 до 1000 символов")
    with connect() as c:
        c.execute(
            "INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
            (username.strip(), password_hash(password), now()),
        )


def obj(row):
    d = dict(row)
    if "labels" in d:
        d["labels"] = json.loads(d["labels"])
    if "data" in d:
        d["data"] = json.loads(d["data"])
    return d


def fail(code, message):
    raise HTTPException(code, message)


def origin_check(req):
    """Отклоняем явно межсайтовые запросы до обработки изменения данных."""
    origin = req.headers.get("origin")
    if origin and (
        urlsplit(origin).scheme not in ("http", "https")
        or urlsplit(origin).netloc != req.headers.get("host")
    ):
        fail(403, "Недопустимый источник запроса")
    if req.headers.get("sec-fetch-site") == "cross-site":
        fail(403, "Недопустимый источник запроса")


def auth(req, mutate=False):
    """Ищем только хеш cookie; для мутаций требуем токен и проверяем источник."""
    if mutate:
        origin_check(req)
    token = req.cookies.get("todo_session", "")
    with connect() as c:
        s = c.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?",
            (digest(token), int(time.time())),
        ).fetchone()
    if not s:
        fail(401, "Требуется вход")
    if mutate and not hmac.compare_digest(
        req.headers.get("x-csrf-token", ""), s["csrf"]
    ):
        fail(403, "Ошибка CSRF")
    return s["owner_id"]


def get_owned(c, table, id, owner):
    """Единая граница доступа: чужой идентификатор выглядит как отсутствующий."""
    row = c.execute(
        f"SELECT * FROM {table} WHERE id=? AND owner_id=?", (id, owner)
    ).fetchone()
    if not row:
        fail(404, "Не найдено")
    return row


def history(c, owner, task, typ, data):
    c.execute(
        "INSERT INTO history(owner_id,task_id,at,type,data) VALUES(?,?,?,?,?)",
        (owner, task, now(), typ, json.dumps(data, ensure_ascii=False)),
    )


class ProjectIn(BaseModel):
    name: str = Field(max_length=200)
    description: str = Field(default="", max_length=20000)
    color: str = "#3451D1"

    @field_validator("name")
    @classmethod
    def name_required(cls, v):
        if not v.strip():
            raise ValueError("Введите название")
        return v.strip()

    @field_validator("color")
    @classmethod
    def valid_color(cls, v):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("Некорректный цвет")
        return v


class TaskIn(BaseModel):
    title: str = Field(max_length=500)
    status: str = "waiting"
    description: str = Field(default="", max_length=50000)
    description_format: str = "text"
    parent_id: str | None = None
    project_id: str | None = None
    labels: list[str] = Field(default_factory=list, max_length=50)
    due_date: str | None = None

    @field_validator("title")
    @classmethod
    def title_required(cls, v):
        if not v.strip():
            raise ValueError("Введите название")
        return v.strip()

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        if v not in ("waiting", "in_progress", "done"):
            raise ValueError("Недопустимый статус")
        return v

    @field_validator("description_format")
    @classmethod
    def valid_format(cls, v):
        if v not in ("text", "markdown"):
            raise ValueError("Недопустимый формат")
        return v

    @field_validator("due_date")
    @classmethod
    def valid_date(cls, v):
        if v is None or v == "":
            return None
        try:
            if date.fromisoformat(v).isoformat() != v:
                raise ValueError()
        except ValueError:
            raise ValueError("Введите дату в формате ГГГГ-ММ-ДД")
        return v

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, v):
        return list(dict.fromkeys(x.strip()[:100] for x in v if x.strip()))


class Login(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1000)
    captcha: str = ""


class Bulk(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)
    due_date: str


class ConfirmDelete(BaseModel):
    ids: list[str] = Field(min_length=1)


# Попытки и одноразовые капчи живут в памяти одного процесса и не содержат паролей.
attempts = {}
ip_attempts = {}
challenges = {}
rate_lock = RLock()


def key(req, username):
    # Пробелы не должны позволять обходить ограничения для одного аккаунта.
    return (req.client.host if req.client else "", username.strip().casefold())


def trim_state():
    """Ограничиваем память и удаляем устаревшие записи даже для случайных логинов."""
    cutoff = time.time() - 900
    for mapping in (attempts, ip_attempts):
        for k in list(mapping):
            mapping[k] = [t for t in mapping[k] if t > cutoff]
            if not mapping[k]:
                del mapping[k]
        while len(mapping) > 5000:
            del mapping[next(iter(mapping))]
    for k, (_, expiry) in list(challenges.items()):
        if expiry < time.time():
            del challenges[k]
    while len(challenges) > 5000:
        del challenges[next(iter(challenges))]


@app.get("/api/captcha")
def captcha(req: Request, username: str = ""):
    k = key(req, username[:200])
    with rate_lock:
        trim_state()
        if len(ip_attempts.get(k[0], [])) >= 30:
            fail(429, "Слишком много попыток. Повторите через 15 минут")
        if len(attempts.get(k, [])) < 3:
            return {"required": False}
        a, b = secrets.randbelow(8) + 1, secrets.randbelow(8) + 1
        challenges[k] = (str(a + b), time.time() + 300)
        return {"required": True, "question": f"{a} + {b} = ?"}


@app.post("/api/login")
def login(req: Request, data: Login, response: Response):
    origin_check(req)
    k = key(req, data.username)
    # Атомарная проверка и запись счётчиков защищает и от параллельных попыток.
    with rate_lock:
        trim_state()
        account = attempts.setdefault(k, [])
        ip = ip_attempts.setdefault(k[0], [])
        if len(account) >= 10 or len(ip) >= 30:
            fail(429, "Слишком много попыток. Повторите через 15 минут")
        if len(account) >= 3:
            challenge = challenges.pop(k, None)
            if (
                not challenge
                or challenge[1] < time.time()
                or not hmac.compare_digest(challenge[0], data.captcha.strip())
            ):
                account.append(time.time())
                ip.append(time.time())
                fail(400, "Решите новую капчу")
        with connect() as c:
            u = c.execute(
                "SELECT * FROM users WHERE username=?", (data.username.strip(),)
            ).fetchone()
            expected = u["password_hash"] if u else "00" * 16 + ":" + "00" * 64
            actual = password_hash(data.password, expected.split(":")[0])
            if not hmac.compare_digest(actual, expected):
                account.append(time.time())
                ip.append(time.time())
                fail(401, "Неверный логин или пароль")
            attempts.pop(k, None)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            c.execute("DELETE FROM sessions WHERE expires_at<?", (int(time.time()),))
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (digest(token), u["id"], csrf, int(time.time()) + 86400 * 30),
            )
    response.set_cookie(
        "todo_session",
        token,
        httponly=True,
        samesite="strict",
        secure=req.url.scheme == "https",
        max_age=86400 * 30,
        path="/",
    )
    return {"username": u["username"], "csrf": csrf}


@app.get("/api/me")
def me(req: Request):
    owner = auth(req)
    with connect() as c:
        s = c.execute(
            "SELECT csrf FROM sessions WHERE token_hash=?",
            (digest(req.cookies["todo_session"]),),
        ).fetchone()
        u = c.execute("SELECT username FROM users WHERE id=?", (owner,)).fetchone()
    return {"username": u["username"], "csrf": s["csrf"]}


@app.post("/api/logout")
def logout(req: Request, response: Response):
    auth(req, True)
    with connect() as c:
        c.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (digest(req.cookies["todo_session"]),),
        )
    response.delete_cookie("todo_session", path="/")
    return {"ok": True}


@app.get("/api/projects")
def projects(req: Request):
    owner = auth(req)
    with connect() as c:
        return [
            obj(x)
            for x in c.execute(
                "SELECT * FROM projects WHERE owner_id=? ORDER BY name COLLATE NOCASE",
                (owner,),
            )
        ]


@app.post("/api/projects")
def project_create(req: Request, p: ProjectIn):
    owner = auth(req, True)
    id = secrets.token_hex(16)
    with connect() as c:
        c.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?,?)",
            (id, owner, p.name, p.description, p.color, now()),
        )
        return obj(get_owned(c, "projects", id, owner))


@app.put("/api/projects/{id}")
def project_update(id: str, req: Request, p: ProjectIn):
    owner = auth(req, True)
    with connect() as c:
        get_owned(c, "projects", id, owner)
        c.execute(
            "UPDATE projects SET name=?,description=?,color=? WHERE id=? AND owner_id=?",
            (p.name, p.description, p.color, id, owner),
        )
        return obj(get_owned(c, "projects", id, owner))


@app.get("/api/tasks")
def tasks(req: Request):
    owner = auth(req)
    with connect() as c:
        return [
            obj(x)
            for x in c.execute(
                "SELECT * FROM tasks WHERE owner_id=? ORDER BY created_at DESC",
                (owner,),
            )
        ]


def validate_links(c, owner, t, id=None):
    """Проверяем владельца, проект и отсутствие циклов до изменения дерева."""
    if t.project_id:
        get_owned(c, "projects", t.project_id, owner)
    if t.parent_id:
        parent = get_owned(c, "tasks", t.parent_id, owner)
        if parent["project_id"] != t.project_id:
            fail(400, "Родитель и подзадача должны принадлежать одному проекту")
        ancestor = parent
        while ancestor:
            if ancestor["id"] == id:
                fail(400, "Циклическая связь задач запрещена")
            ancestor = (
                get_owned(c, "tasks", ancestor["parent_id"], owner)
                if ancestor["parent_id"]
                else None
            )
    if (
        id
        and c.execute(
            "SELECT 1 FROM tasks WHERE owner_id=? AND parent_id=? AND project_id IS NOT ?",
            (owner, id, t.project_id),
        ).fetchone()
    ):
        fail(400, "Нельзя изменить проект: подзадачи останутся в другом проекте")


def save_task(c, owner, t, id=None):
    """Сохранение и история входят в транзакцию вызывающей операции."""
    validate_links(c, owner, t, id)
    values = (
        t.title,
        t.status,
        t.description,
        t.description_format,
        t.parent_id,
        t.project_id,
        json.dumps(t.labels, ensure_ascii=False),
        t.due_date,
    )
    if id:
        old = get_owned(c, "tasks", id, owner)
        c.execute(
            "UPDATE tasks SET title=?,status=?,description=?,description_format=?,parent_id=?,project_id=?,labels=?,due_date=? WHERE id=? AND owner_id=?",
            (*values, id, owner),
        )
        for col, typ in [("status", "status_changed"), ("due_date", "due_changed")]:
            if old[col] != getattr(t, col):
                history(c, owner, id, typ, {"from": old[col], "to": getattr(t, col)})
        if old["status"] != "done" and t.status == "done" and t.parent_id:
            history(c, owner, t.parent_id, "subtask_done", {"title": t.title, "id": id})
        history(c, owner, id, "updated", {})
    else:
        id = secrets.token_hex(16)
        c.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (id, owner, *values, now()),
        )
        history(c, owner, id, "created", {})
    return obj(get_owned(c, "tasks", id, owner))


@app.post("/api/tasks")
def task_create(req: Request, t: TaskIn):
    owner = auth(req, True)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        return save_task(c, owner, t)


@app.post("/api/tasks/bulk-due")
def bulk(req: Request, b: Bulk):
    owner = auth(req, True)
    try:
        if date.fromisoformat(b.due_date).isoformat() != b.due_date:
            raise ValueError()
    except ValueError:
        fail(400, "Некорректная дата")
    if len(set(b.ids)) != len(b.ids):
        fail(400, "Повторяющиеся задачи")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = [get_owned(c, "tasks", id, owner) for id in b.ids]
        for row in rows:
            c.execute(
                "UPDATE tasks SET due_date=? WHERE id=? AND owner_id=?",
                (b.due_date, row["id"], owner),
            )
            history(
                c,
                owner,
                row["id"],
                "due_changed",
                {"from": row["due_date"], "to": b.due_date},
            )
    return {"ok": True}


@app.get("/api/tasks/{id}")
def task_get(id: str, req: Request):
    owner = auth(req)
    with connect() as c:
        task = obj(get_owned(c, "tasks", id, owner))
        task["history"] = [
            obj(x)
            for x in c.execute(
                "SELECT * FROM history WHERE owner_id=? AND task_id=? ORDER BY id DESC",
                (owner, id),
            )
        ]
        return task


@app.put("/api/tasks/{id}")
def task_update(id: str, req: Request, t: TaskIn):
    owner = auth(req, True)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        get_owned(c, "tasks", id, owner)
        return save_task(c, owner, t, id)


@app.post("/api/tasks/{id}/duplicate")
def duplicate(id: str, req: Request):
    owner = auth(req, True)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        t = obj(get_owned(c, "tasks", id, owner))
        t["status"] = "waiting"
        return save_task(c, owner, TaskIn(**t))


def tree(c, owner, id):
    """Полный снимок дерева для предварительного просмотра и проверки подтверждения."""
    get_owned(c, "tasks", id, owner)
    return [
        obj(x)
        for x in c.execute(
            "WITH RECURSIVE tree(id) AS (SELECT id FROM tasks WHERE id=? AND owner_id=? UNION ALL SELECT t.id FROM tasks t JOIN tree ON t.parent_id=tree.id WHERE t.owner_id=?) SELECT tasks.* FROM tasks JOIN tree ON tasks.id=tree.id WHERE tasks.owner_id=? ORDER BY tasks.created_at,tasks.id",
            (id, owner, owner, owner),
        )
    ]


@app.get("/api/tasks/{id}/delete-preview")
def delete_preview(id: str, req: Request):
    owner = auth(req)
    with connect() as c:
        return tree(c, owner, id)


@app.post("/api/tasks/{id}/delete")
def delete_task(id: str, req: Request, body: ConfirmDelete):
    owner = auth(req, True)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = tree(c, owner, id)
        if len(body.ids) != len(rows) or set(body.ids) != {x["id"] for x in rows}:
            fail(409, "Состав дерева изменился. Проверьте список заново")
        # Потомков удаляем раньше родителей: SQLite ограничивает глубину
        # каскадных триггеров, а дерево приложения может быть сколь угодно глубоким.
        remaining = {row["id"]: row["parent_id"] for row in rows}
        children = {key: 0 for key in remaining}
        for parent in remaining.values():
            if parent in children:
                children[parent] += 1
        leaves = [key for key, count in children.items() if count == 0]
        while leaves:
            leaf = leaves.pop()
            c.execute("DELETE FROM tasks WHERE id=? AND owner_id=?", (leaf, owner))
            parent = remaining[leaf]
            if parent in children:
                children[parent] -= 1
                if children[parent] == 0:
                    leaves.append(parent)
    return {"ok": True}


@app.get("/health")
def health():
    with connect() as c:
        c.execute("SELECT 1").fetchone()
    return {"status": "ok"}


class MarkdownIn(BaseModel):
    text: str = Field(max_length=50000)


@app.post("/api/markdown")
def render_markdown(req: Request, body: MarkdownIn):
    auth(req, True)
    # Исходный HTML экранируется до преобразования; затем разрешён только узкий набор тегов.
    rendered = markdown.markdown(html.escape(body.text), extensions=["fenced_code"])
    safe = bleach.clean(
        rendered,
        tags={
            "p",
            "br",
            "strong",
            "em",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ul",
            "ol",
            "li",
            "code",
            "pre",
            "blockquote",
            "a",
            "hr",
        },
        attributes={"a": ["href", "title"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    return {"html": safe}


@app.get("/{path:path}")
def frontend(path: str):
    return FileResponse(Path(__file__).parent / "static" / "index.html")
