"""Acceptance of project editor, typography and concrete history events; headless only."""

import json
import os
import re
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get("TODO_QA_URL", "http://127.0.0.1:18081")
OUT = Path(os.environ.get("TODO_QA_OUTPUT", "artifacts/browser-final"))
OUT.mkdir(parents=True, exist_ok=True)
client = httpx.Client(base_url=BASE)
login = client.post(
    "/api/login", json={"username": "qa_a", "password": "qa-password-one"}
)
login.raise_for_status()
client.headers["X-CSRF-Token"] = login.json()["csrf"]
checks = []
errors = []


def record(name):
    checks.append(name)


def request(method, path, **kwargs):
    result = client.request(method, "/api" + path, **kwargs)
    result.raise_for_status()
    return result.json()


with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True, executable_path=os.environ.get("TODO_QA_CHROMIUM")
    )
    page = browser.new_page(viewport={"width": 1714, "height": 1243}, locale="ru-RU")
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(BASE)
        page.locator("[name=username]").fill("qa_a")
        page.locator("[name=password]").fill("qa-password-one")
        page.get_by_role("button", name="Войти", exact=True).click()
        page.wait_for_selector(".home")
        page.goto(BASE + "/tasks/new")
        task_width = page.locator("#task-form").bounding_box()["width"]
        page.goto(BASE + "/projects")
        page.get_by_role("button", name=re.compile("Создать новый проект")).click()
        dialog = page.locator("dialog.project-dialog")
        expect(dialog).to_be_visible()
        assert abs(dialog.bounding_box()["width"] - task_width) <= 2
        page.evaluate("document.fonts.ready")
        assert page.evaluate(
            "document.fonts.check('14px Inter') && document.fonts.check('700 24px Manrope')"
        )
        for selector in ["label", "button", "input", "textarea", ".color-recent-title"]:
            assert dialog.locator(selector).evaluate_all(
                "els => els.every(e => getComputedStyle(e).fontFamily.startsWith('Inter'))"
            ), selector
        assert "Manrope" in dialog.locator("h2").evaluate(
            "e=>getComputedStyle(e).fontFamily"
        )
        assert dialog.locator("textarea").bounding_box()["height"] >= 180
        record(
            "Project dialog matches task width; fonts load and inherit across fields/buttons/text"
        )
        assert dialog.locator(".color-palette [data-color]").count() == 10
        assert dialog.locator(".color-recent > *").count() == 20
        expect(dialog.locator(".color-rgb")).not_to_be_visible()
        dialog.locator("#project-name").fill("Аннотации: проект")
        dialog.locator('[data-project-format="markdown"]').click()
        dialog.locator("textarea").fill(
            "**Описание**\n\n- Первый пункт\n- Второй пункт"
        )
        expect(dialog.locator(".editor-preview strong")).to_have_text("Описание")
        for label in ["метка", "временная"]:
            dialog.locator("#project-label-input").fill(label)
            dialog.locator("#project-label-input").press("Enter")
        dialog.get_by_role("button", name="Удалить метку временная", exact=True).click()
        dialog.locator('[data-color-tab="rgb"]').click()
        expect(dialog.locator(".color-palette")).not_to_be_visible()
        expect(dialog.locator(".color-recent")).to_be_visible()
        for channel, value in [("red", "17"), ("green", "34"), ("blue", "51")]:
            dialog.locator(f'[name="{channel}"]').fill(value)
        expect(dialog.locator('[name="hex"]')).to_have_value("#112233")
        page.screenshot(path=str(OUT / "project-editor-desktop.png"), full_page=True)
        dialog.get_by_role("button", name="Сохранить", exact=True).click()
        page.wait_for_url(re.compile(r"/projects/[0-9a-f]+$"))
        project = next(
            x for x in request("GET", "/projects") if x["name"] == "Аннотации: проект"
        )
        assert project["color"] == "#112233" and project["labels"] == ["метка"]
        assert project["description_format"] == "markdown"
        expect(page.locator(".project-description strong")).to_have_text("Описание")
        record("Markdown preview/rendering, label add/remove and RGB save")
        page.reload()
        page.get_by_role("button", name="Изменить", exact=True).click()
        expect(dialog.locator("textarea")).to_have_value(project["description"])
        assert (
            dialog.locator(".color-recent [data-color]").first.get_attribute(
                "data-color"
            )
            == "#112233"
        )
        assert (
            dialog.locator("#project-label-options option[value='метка']").count() == 1
        )
        dialog.locator('[data-color-tab="rgb"]').click()
        dialog.locator('[name="red"]').fill("999")
        dialog.locator('[data-color-tab="palette"]').click()
        dialog.get_by_role("button", name="Сохранить", exact=True).click()
        expect(dialog.locator(".error")).to_have_text(
            "Введите корректные значения RGB и HEX"
        )
        dialog.locator('.color-palette [data-color="#1D7A4C"]').click()
        dialog.get_by_role("button", name="Сохранить", exact=True).click()
        expect(dialog).to_have_count(0)
        assert request("GET", "/project-colors")[0] == "#1D7A4C"
        page.get_by_role("button", name="Изменить", exact=True).click()
        dialog.locator('[data-color-tab="rgb"]').click()
        dialog.locator('[name="hex"]').fill("#abcdef")
        expect(dialog.locator('[name="hex"]')).to_have_value("#ABCDEF")
        dialog.get_by_role("button", name="Отмена", exact=True).click()
        expect(dialog).to_have_count(0)
        assert "#ABCDEF" not in request("GET", "/project-colors")
        record("Reload, autocomplete, invalid hidden RGB recovery and cancellation")
        colors = [f"#{i:06X}" for i in range(25)]
        for color in colors:
            request(
                "PUT", "/projects/" + project["id"], json={**project, "color": color}
            )
        assert request("GET", "/project-colors") == list(reversed(colors[-20:]))
        page.reload()
        page.get_by_role("button", name="Изменить", exact=True).click()
        expect(dialog.locator(".color-recent > *")).to_have_count(20)
        for width, height in [(1714, 1243), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            cells = dialog.locator(".color-recent > *").evaluate_all(
                "els=>els.map(e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y}))"
            )
            rows = sorted(set(round(c["y"]) for c in cells))
            assert len(rows) == 2 and all(
                sum(round(c["y"]) == y for c in cells) == 10 for y in rows
            )
            assert dialog.evaluate("e=>e.scrollWidth<=e.clientWidth+1")
            bounds = dialog.bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            page.screenshot(
                path=str(OUT / f"project-editor-{width}.png"), full_page=True
            )
        dialog.get_by_role("button", name="Отмена", exact=True).click()
        record("Persistent 20-color MRU and 10-by-2 layout on desktop/mobile")
        page.set_viewport_size({"width": 1714, "height": 1243})
        task = request("POST", "/tasks", json={"title": "Проверка истории"})
        page.goto(BASE + "/tasks/" + task["id"])
        page.get_by_role("button", name="В работе", exact=True).click()
        expect(page.locator(".history-item")).to_have_count(2)
        assert page.locator(".history-item b").all_text_contents() == [
            "Смена статуса",
            "Создана",
        ]
        page.locator(".task-menu summary").click()
        page.get_by_role("button", name="Удалить", exact=True).click()
        confirmation = page.locator("dialog")
        expect(confirmation).to_be_visible()
        assert confirmation.locator("p,li,button").evaluate_all(
            "els=>els.length>0 && els.every(e=>getComputedStyle(e).fontFamily.startsWith('Inter'))"
        )
        page.screenshot(path=str(OUT / "confirmation-fonts.png"), full_page=True)
        confirmation.get_by_role("button", name="Отмена", exact=True).click()
        record(
            "Status action adds only its event; confirmation body/list/buttons use Inter"
        )
        assert not errors, errors
        record("No JavaScript errors")
    finally:
        page.screenshot(path=str(OUT / "annotations-last.png"), full_page=True)
        (OUT / "annotations-report.json").write_text(
            json.dumps(
                {"checks": checks, "errors": errors}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        browser.close()
print(
    json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=True, indent=2)
)
