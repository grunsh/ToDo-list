"""Фоновая приёмка на одноразовой БД: реальные HTTP-запросы и Chromium."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from playwright.sync_api import sync_playwright

BASE = os.environ.get("TODO_QA_URL", "http://127.0.0.1:18081")
OUT = Path(
    os.environ.get(
        "TODO_QA_OUTPUT",
        str(Path(__file__).resolve().parents[1] / "artifacts" / "browser-final"),
    )
)
OUT.mkdir(parents=True, exist_ok=True)
results = []
errors = []
today = (datetime.now().astimezone().date()).isoformat()
# Сервер и UI используют московскую календарную дату; Windows может не иметь tzdata.
today = (
    (datetime.now(__import__("datetime").timezone.utc) + timedelta(hours=3))
    .date()
    .isoformat()
)
tomorrow = (
    __import__("datetime").date.fromisoformat(today) + timedelta(days=1)
).isoformat()
yesterday = (
    __import__("datetime").date.fromisoformat(today) - timedelta(days=1)
).isoformat()
future = (
    __import__("datetime").date.fromisoformat(today) + timedelta(days=5)
).isoformat()

client = httpx.Client(base_url=BASE)
login = client.post(
    "/api/login", json={"username": "qa_a", "password": "qa-password-one"}
)
login.raise_for_status()
client.headers["X-CSRF-Token"] = login.json()["csrf"]


def post(path, body):
    response = client.post("/api" + path, json=body)
    response.raise_for_status()
    return response.json()


work = post(
    "/projects",
    {
        "name": "Работа",
        "description": "Рабочие задачи, отчёты и встречи с командой.",
        "color": "#3451D1",
    },
)
house = post(
    "/projects", {"name": "Дом", "description": "Домашние дела", "color": "#1D7A4C"}
)
root = post(
    "/tasks",
    {
        "title": "Подготовить квартальный отчёт",
        "project_id": work["id"],
        "status": "in_progress",
        "due_date": yesterday,
        "description": "**Квартальный** отчёт для руководителя.\n\n### Что собрать\n- выручку\n- планы\n\n`Отчёты/2026`",
        "description_format": "markdown",
        "labels": ["срочно", "отчёт"],
    },
)
child = post(
    "/tasks",
    {
        "title": "Собрать данные из CRM",
        "project_id": work["id"],
        "parent_id": root["id"],
        "due_date": today,
        "status": "done",
    },
)
child2 = post(
    "/tasks",
    {
        "title": "Построить графики",
        "project_id": work["id"],
        "parent_id": root["id"],
        "due_date": tomorrow,
    },
)
post(
    "/tasks",
    {
        "title": "Позвонить в банк — уточнить лимит",
        "project_id": work["id"],
        "due_date": today,
    },
)
post("/tasks", {"title": "Забрать посылку на почте", "due_date": today})
post("/tasks", {"title": "Разобрать фотографии", "labels": ["идея"]})
post(
    "/tasks",
    {
        "title": "Оплатить интернет",
        "due_date": yesterday,
        "labels": ["счета"],
        "description": "Домашний интернет, лицевой счёт в приложении провайдера.",
    },
)
post(
    "/tasks",
    {
        "title": "Купить продукты на неделю",
        "project_id": house["id"],
        "due_date": tomorrow,
    },
)
post(
    "/tasks", {"title": "Будущий проект", "project_id": work["id"], "due_date": future}
)
post("/tasks", {"title": "Завершено без срока", "status": "done"})
duplicate_parents = [
    post("/tasks", {"title": "Одинаковое название", "project_id": work["id"]})
    for _ in range(2)
]
for i in range(15):
    post("/tasks", {"title": f"Задача на сегодня {i+1}", "due_date": today})

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True, executable_path=os.environ.get("TODO_QA_CHROMIUM")
    )
    page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="ru-RU")
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on(
        "dialog",
        lambda d: (errors.append("unexpected dialog: " + d.message), d.dismiss()),
    )

    def check(name, fn):
        try:
            fn()
            results.append({"name": name, "passed": True})
        except Exception as e:
            results.append({"name": name, "passed": False, "error": str(e)})
            page.screenshot(
                path=str(OUT / ("failed-" + str(len(results)) + ".png")), full_page=True
            )

    def navigate(path):
        page.goto(BASE + path)
        page.wait_for_selector(".main")
        page.wait_for_timeout(150)

    def enter():
        page.goto(BASE)
        page.locator("[name=username]").fill("qa_a")
        page.locator("[name=password]").fill("qa-password-one")
        page.get_by_role("button", name="Войти", exact=True).click()
        page.wait_for_selector(".home")

    check("Вход и главная", enter)

    def home_layout():
        for width, height in [(1440, 900), (1920, 1080), (2560, 1440)]:
            page.set_viewport_size({"width": width, "height": height})
            navigate("/")
            assert page.locator(".widget").count() == 4
            dimensions = page.evaluate(
                "({scroll:document.documentElement.scrollHeight,height:innerHeight,width:document.documentElement.scrollWidth,viewport:innerWidth})"
            )
            assert dimensions["scroll"] <= height + 1, dimensions
            assert dimensions["width"] <= width + 1, dimensions
            assert (
                page.locator(".widget")
                .first.locator(".rows")
                .evaluate("(e)=>e.scrollHeight>e.clientHeight")
            )
            page.screenshot(path=str(OUT / f"home-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})

    check("Главная: четыре блока и внутренняя прокрутка", home_layout)

    def plan_rules():
        navigate("/")
        text = page.locator(".widget").first.inner_text()
        assert "Разобрать фотографии" in text
        assert "Подготовить квартальный отчёт" not in text
        assert "Будущий проект" not in text
        assert "Завершено без срока" not in text

    check("Сегодня и открытые без срока", plan_rules)

    def footer_filter():
        navigate("/")
        page.locator(".widget").nth(1).locator(".wfoot a").click()
        page.wait_for_url("**/tasks?date=overdue")
        assert (
            page.locator(".rows").get_by_text("Оплатить интернет", exact=True).count()
            == 1
        )
        assert (
            page.locator(".rows")
            .get_by_text("Разобрать фотографии", exact=True)
            .count()
            == 0
        )

    check("Фильтр по ссылке с главной", footer_filter)

    for name, path in [
        ("all-tasks", "/tasks"),
        ("no-project", "/tasks/no-project"),
        ("projects", "/projects/" + work["id"]),
        ("task-card", "/tasks/" + root["id"]),
        ("task-new", "/tasks/new"),
        ("task-edit", "/tasks/" + root["id"] + "/edit"),
    ]:

        def screenshot(path=path, name=name):
            navigate(path)
            page.screenshot(path=str(OUT / (name + ".png")), full_page=True)

        check("Экран " + name, screenshot)

    def preview_selection():
        navigate("/tasks/no-project")
        row = page.locator(".trow").filter(has_text="Оплатить интернет")
        row.click(position={"x": row.bounding_box()["width"] - 5, "y": 25})
        assert page.url.endswith("/tasks/no-project")
        assert "Оплатить интернет" in page.locator(".peek").inner_text()

    check("Быстрый просмотр без ухода со списка", preview_selection)

    def quick_add():
        navigate("/")
        page.locator("#quick").fill("Приёмка: быстрая задача")
        page.locator("#quick").press("Enter")
        page.wait_for_timeout(250)
        item = next(
            t
            for t in client.get("/api/tasks").json()
            if t["title"] == "Приёмка: быстрая задача"
        )
        assert (
            item["due_date"] == today
            and item["project_id"] is None
            and item["status"] == "waiting"
        )

    check("Быстрое добавление задачи на сегодня", quick_add)

    def transfer_confirm():
        navigate("/tasks")
        toggle = page.get_by_role(
            "button", name=__import__("re").compile("Прокрастинация")
        )
        if not page.locator(".pbar").count():
            toggle.click()
        page.locator(".pbar label input[type=checkbox]").check()
        assert page.locator(".pick:checked").count() > 1
        page.locator(".pbar label input[type=checkbox]").uncheck()
        assert page.locator(".pick:checked").count() == 0
        row = page.locator(".trow").filter(has_text="Разобрать фотографии")
        row.locator("input[type=checkbox]").check()
        target = page.locator(".pbar").get_by_role("button", name="Завтра", exact=True)
        assert (
            target.evaluate("(e)=>getComputedStyle(e).backgroundColor")
            != "rgb(255, 255, 255)"
        )
        target.click()
        dialog = page.locator("dialog")
        assert "Разобрать фотографии" in dialog.inner_text()
        before = next(
            t
            for t in client.get("/api/tasks").json()
            if t["title"] == "Разобрать фотографии"
        )
        assert before["due_date"] is None
        dialog.get_by_role("button", name="Отмена").click()
        target.click()
        page.locator("dialog").get_by_role("button", name="Подтвердить").click()
        page.wait_for_timeout(200)
        assert client.get("/api/tasks/" + before["id"]).json()["due_date"] == tomorrow

    check("Перенос: тёмные кнопки, список, отмена и подтверждение", transfer_confirm)

    def form_create():
        navigate("/tasks/new")
        page.get_by_role("button", name="Создать задачу", exact=True).click()
        assert page.locator("#form-error").inner_text() == "Введите название"
        page.locator("[name=title]").fill("Приёмка: форма")
        page.get_by_role("button", name="Markdown", exact=True).click()
        page.locator("[name=description]").fill(
            "**Жирный текст**\n<script>window.pwned=1</script>"
        )
        page.wait_for_timeout(300)
        assert page.locator(".editor-preview strong").inner_text() == "Жирный текст"
        assert not page.evaluate("window.pwned")
        page.locator("#tag-input").fill("проверка")
        page.locator("#tag-input").press("Enter")
        page.get_by_role("button", name="Завтра", exact=True).click()
        page.get_by_role("button", name="Создать задачу", exact=True).click()
        page.wait_for_url(__import__("re").compile(r"/tasks/[0-9a-f]+$"))
        item = next(
            t for t in client.get("/api/tasks").json() if t["title"] == "Приёмка: форма"
        )
        assert item["due_date"] == tomorrow and "проверка" in item["labels"]
        page.get_by_role("link", name="Редактировать", exact=True).click()
        page.locator("[name=title]").fill("Приёмка: форма изменена")
        page.locator("[name=title]").press("Control+Enter")
        page.wait_for_url(__import__("re").compile(r"/tasks/[0-9a-f]+$"))
        assert (
            client.get("/api/tasks/" + item["id"]).json()["title"]
            == "Приёмка: форма изменена"
        )

    check("Форма, Markdown, метки, правка, Ctrl+Enter", form_create)

    def project_crud():
        navigate("/projects")
        page.get_by_role(
            "button", name=__import__("re").compile("Создать новый проект")
        ).click()
        page.locator("dialog [name=name]").fill("Проект приёмки")
        page.locator("dialog [name=description]").fill("Описание проекта")
        page.locator("dialog").get_by_role(
            "button", name="Сохранить", exact=True
        ).click()
        page.wait_for_url(__import__("re").compile(r"/projects/[0-9a-f]+$"))
        page.get_by_role("button", name="Изменить", exact=True).click()
        page.locator("dialog [name=name]").fill("Проект после правки")
        page.locator("dialog").get_by_role(
            "button", name="Сохранить", exact=True
        ).click()
        page.wait_for_timeout(200)
        assert any(
            x["name"] == "Проект после правки"
            for x in client.get("/api/projects").json()
        )

    check("Создание и редактирование проекта", project_crud)

    def combo_selection():
        navigate("/tasks/new")
        page.locator("[name=title]").fill("Приёмка: подзадача одинакового названия")
        combo = page.locator("[data-combo=project]")
        combo.fill("Раб")
        assert page.locator(".combo-menu mark").count() >= 1
        combo.press("Enter")
        assert page.locator("[name=project_id]").input_value() == work["id"]
        parent = page.locator("[data-combo=parent]")
        parent.fill("Одинаковое название")
        opts = page.evaluate("comboItems.map(x=>x.id)")
        assert len(opts) == 2
        parent.press("ArrowDown")
        parent.press("Enter")
        assert page.locator("[name=parent_id]").input_value() == opts[1]
        page.get_by_role("button", name="Создать задачу", exact=True).click()
        page.wait_for_url(__import__("re").compile(r"/tasks/[0-9a-f]+$"))
        created = next(
            t
            for t in client.get("/api/tasks").json()
            if t["title"] == "Приёмка: подзадача одинакового названия"
        )
        assert created["parent_id"] == opts[1]
        navigate("/tasks/new")
        combo = page.locator("[data-combo=project]")
        combo.fill("Новый проект из формы")
        combo.press("Enter")
        page.wait_for_timeout(200)
        assert any(
            x["name"] == "Новый проект из формы"
            for x in client.get("/api/projects").json()
        )
        combo.focus()
        combo.press("Escape")
        assert not combo.locator("..").locator(".combo-menu").is_visible()

    check("Комбобоксы: клавиатура, стабильные ID, создание проекта", combo_selection)

    def tree_controls():
        navigate("/projects/" + work["id"])
        parentrow = page.locator(".trow").filter(has_text=root["title"])
        toggle = parentrow.locator(".tree-toggle")
        if not page.locator(".trow").filter(has_text=child2["title"]).count():
            toggle.click()
        assert page.locator(".trow").filter(has_text=child2["title"]).count() == 1
        toggle.click()
        assert page.locator(".trow").filter(has_text=child2["title"]).count() == 0
        toggle.click()
        row = page.locator(".trow").filter(has_text=child2["title"])
        row.locator(".st").click()
        page.wait_for_timeout(150)
        assert (
            client.get("/api/tasks/" + child2["id"]).json()["status"] == "in_progress"
        )
        row.locator(".st").click()
        page.wait_for_timeout(150)
        assert client.get("/api/tasks/" + child2["id"]).json()["status"] == "done"
        parentdata = client.get("/api/tasks/" + root["id"]).json()
        assert parentdata["status"] == "in_progress"
        assert any(h["type"] == "subtask_done" for h in parentdata["history"])

    check("Дерево и переключение статуса подзадачи", tree_controls)

    def delete_confirm():
        navigate("/tasks/" + root["id"] + "/edit")
        page.get_by_role("button", name="Удалить задачу", exact=True).click()
        dialog = page.locator("dialog")
        for title in [root["title"], child["title"], child2["title"]]:
            assert title in dialog.inner_text()
        dialog.get_by_role("button", name="Отмена").click()
        assert client.get("/api/tasks/" + root["id"]).status_code == 200
        page.get_by_role("button", name="Удалить задачу", exact=True).click()
        page.locator("dialog").get_by_role("button", name="Подтвердить").click()
        page.wait_for_timeout(200)
        assert client.get("/api/tasks/" + child["id"]).status_code == 404

    check("Удаление дерева: список потомков, отмена, подтверждение", delete_confirm)

    def mobile():
        page.set_viewport_size({"width": 390, "height": 844})
        for route in ["/", "/tasks", "/tasks/new", "/projects"]:
            navigate(route)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), route
        page.screenshot(path=str(OUT / "mobile.png"), full_page=True)

    check("Мобильный вид без горизонтальной прокрутки", mobile)

    def logout_mobile():
        assert page.locator(".logout").is_visible()
        page.locator(".logout").click()
        page.wait_for_selector("#login")
        assert page.locator(".trow").count() == 0

    check("Выход доступен на мобильном экране", logout_mobile)
    check(
        "Нет ошибок JavaScript",
        lambda: (_ for _ in ()).throw(AssertionError(errors)) if errors else None,
    )
    browser.close()

report = {
    "checks": results,
    "errors": errors,
    "passed": all(r["passed"] for r in results),
}
(OUT / "browser-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=True, indent=2))
raise SystemExit(0 if report["passed"] else 1)
