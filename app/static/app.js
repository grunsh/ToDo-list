// Данные интерфейса получаются только из API текущего пользователя.
const $ = (s) => document.querySelector(s),
  esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
let me = null,
  tasks = [],
  projects = [],
  selected = null,
  opened = {},
  checks = {},
  view = "list",
  statusFilter = "",
  dateFilter = new URLSearchParams(location.search).get("date") || "",
  projectFilter = "",
  query = "",
  sort = "due",
  csrf = "",
  formStatus = "waiting",
  formFormat = "text",
  labels = [];
const statuses = {
  waiting: "Ожидает",
  in_progress: "В работе",
  done: "Исполнено",
};
const today = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
function addDays(n) {
  let d = new Date(today() + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
async function api(path, method = "GET", body) {
  let r = await fetch("/api" + path, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let d = await r.json();
  if (!r.ok)
    throw Error(
      typeof d.detail === "string" ? d.detail : "Проверьте заполнение полей",
    );
  return d;
}
function syncRoute() {
  dateFilter = new URLSearchParams(location.search).get("date") || "";
  statusFilter = "";
  projectFilter = "";
  query = "";
  selected = null;
}
function go(path) {
  history.pushState({}, "", path);
  syncRoute();
  render();
}
window.onpopstate = () => {
  syncRoute();
  render();
};
document.addEventListener("click", (e) => {
  let a = e.target.closest("a[data-go]");
  if (a) {
    e.preventDefault();
    go(a.getAttribute("href"));
  }
});
function button(label, onclick, cls = "btn") {
  return `<button type="button" class="${cls}" onclick="${onclick}">${label}</button>`;
}
const icons = {
  home: '<path d="M4 11l8-6.5 8 6.5M6 9.5V19h4v-5h4v5h4V9.5"/>',
  inbox: '<path d="M4 13l2-7h12l2 7v6H4zM4 13h5l1 2h4l1-2h5"/>',
  projects:
    '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  all: '<path d="M12 4l8.5 4.5L12 13 3.5 8.5zM3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5"/>',
  today:
    '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
  late: '<path d="M12 4.5l9 15.5H3zM12 10v4.5M12 17.2v.1"/>',
  tomorrow:
    '<path d="M5 17a7 7 0 0 1 14 0M3 20h18M12 4v4M8.5 9.5l-1.4-1.4M15.5 9.5l1.4-1.4"/>',
};
function icon(name) {
  return `<svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.all}</svg>`;
}
function stats(arr, includeLate = false) {
  return `<div class="stats">${Object.entries(statuses)
    .map(
      ([k, v]) =>
        `<div class="stat"><b style="color:${k === "done" ? "var(--ok)" : k === "in_progress" ? "var(--accent)" : "var(--ink)"}">${arr.filter((t) => t.status === k).length}</b><span>${k === "done" ? "●" : k === "in_progress" ? "◐" : "○"} ${v}</span></div>`,
    )
    .join(
      "",
    )}${includeLate ? `<div class="stat"><b style="color:var(--warn)">${arr.filter(overdue).length}</b><span>Просрочено</span></div>` : ""}</div>`;
}
function shell(content, home = false) {
  return `<div class="app"><aside class="side"><div class="brand"><span class="logo">✓</span>To-Do</div><a data-go class="btn pri" href="/tasks/new">＋ Новая задача</a><nav class="nav"><a data-go href="/">${icon("home")}<span>Главная</span></a><a data-go href="/tasks/no-project">${icon("inbox")}<span>Внепроектные задачи</span> <span class="cnt">${tasks.filter((t) => !t.project_id).length}</span></a><a data-go href="/projects">${icon("projects")}<span>Проекты</span> <span class="cnt">${projects.length}</span></a><a data-go href="/tasks">${icon("all")}<span>Все задачи</span> <span class="cnt">${tasks.length}</span></a></nav><nav class="nav"><div class="navh">Проекты</div>${projects.map((p) => `<a data-go href="/projects/${p.id}"><span class="dot" style="background:${esc(p.color)}"></span>${esc(p.name)}</a>`).join("")}</nav><button class="btn logout" onclick="logout()">Выйти · ${esc(me.username)}</button></aside><main class="main ${home ? "home" : ""}">${content}</main></div>`;
}
async function logout() {
  await api("/logout", "POST", {});
  me = null;
  csrf = "";
  tasks = [];
  projects = [];
  opened = {};
  checks = {};
  syncRoute();
  render();
}
function loginPage(msg = "") {
  return `<div class="app"><div class="card login"><div class="brand"><span class="logo">✓</span>To-Do</div><h1>Вход</h1><form id="login"><label class="field">Логин <input class="input" name="username" required autocomplete="username"></label><label class="field">Пароль <input class="input" name="password" type="password" required autocomplete="current-password"></label><div id="captcha"></div><div class="error">${esc(msg)}</div><button class="btn pri">Войти</button></form></div></div>`;
}
async function checkCaptcha() {
  let name = $("#login [name=username]").value;
  let c = await api("/captcha?username=" + encodeURIComponent(name));
  $("#captcha").innerHTML = c.required
    ? `<label class="field">${esc(c.question)} <input class="input" name="captcha" required autocomplete="off"></label>`
    : "";
}
async function loginSubmit(e) {
  e.preventDefault();
  let f = new FormData(e.target);
  try {
    me = await api("/login", "POST", Object.fromEntries(f));
    csrf = me.csrf;
    await refresh();
    go("/");
  } catch (err) {
    $(".error").textContent = err.message;
    await checkCaptcha();
  }
}
async function refresh() {
  [tasks, projects] = await Promise.all([api("/tasks"), api("/projects")]);
}
function proj(id) {
  return projects.find((x) => x.id === id);
}
function dateText(t) {
  if (!t.due_date) return "Без даты";
  if (t.due_date === today()) return "Сегодня";
  if (t.due_date === addDays(1)) return "Завтра";
  return new Date(t.due_date + "T12:00:00Z").toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
function overdue(t) {
  return t.due_date && t.due_date < today() && t.status !== "done";
}
function row(t, list, depth = 0, tree = false) {
  let children = tasks.filter((x) => x.parent_id === t.id);
  let done = children.filter((x) => x.status === "done").length;
  let sym =
    t.status === "done"
      ? '<svg width="22" height="22" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.5" fill="#1D7A4C"/><path d="M7.5 12.3l3 3 6-6.3" stroke="white" stroke-width="2.2" fill="none"/></svg>'
      : t.status === "in_progress"
        ? '<svg width="22" height="22" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="#3451D1" stroke-width="1.8"/><path d="M12 6.5a5.5 5.5 0 0 1 0 11z" fill="#3451D1"/></svg>'
        : '<svg width="22" height="22" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="#9A9BA2" stroke-width="1.8"/></svg>';
  return `<div class="trow ${t.status === "done" ? "done" : ""} ${selected === t.id ? "sel" : ""}" style="padding-left:${16 + depth * 20}px" onclick="if(!event.target.closest('a,button,input')){selected='${t.id}';render()}">${tree ? (children.length ? button(opened[t.id] ? "⌄" : "›", `toggleTree('${t.id}')`, "tree-toggle") : '<span class="tw"></span>') : ""}${opened[list] ? `<input class="cb pick" type="checkbox" data-list="${list}" value="${t.id}" ${checks[list]?.includes(t.id) ? "checked" : ""} onchange="pick(this)">` : ""}<button class="st ${t.status === "done" ? "completed" : t.status === "in_progress" ? "progressing" : ""}" title="${statuses[t.status]}" onclick="cycle('${t.id}')">${sym}</button><a class="t" data-go href="/tasks/${t.id}" onclick="selected='${t.id}'">${esc(t.title)}</a><span class="meta">${list === "home-late" && overdue(t) ? `<span class="chip hot">${Math.floor((new Date(today()) - new Date(t.due_date)) / 86400000)} дн.</span>` : ""}${t.labels.map((l) => `<span class="chip">${esc(l)}</span>`).join("")}${children.length ? `<span class="chip">${done}/${children.length}</span>` : ""}${list === "project" ? `<span class="date">создана ${new Date(t.created_at).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", timeZone: "Europe/Moscow" })}</span>` : ""}${proj(t.project_id) ? `<span class="proj"><span class="dot" style="background:${esc(proj(t.project_id).color)}"></span>${esc(proj(t.project_id).name)}</span>` : ""}<span class="date ${overdue(t) ? "od" : t.due_date === today() ? "today" : ""}">${dateText(t)}</span></span></div>`;
}
// Видимые ветви строятся из идентификаторов; выбор родителя не выбирает потомков.
function treeRows(arr, list) {
  let ids = new Set(arr.map((t) => t.id));
  function branch(t, d) {
    return (
      row(t, list, d, true) +
      (opened[t.id]
        ? arr
            .filter((x) => x.parent_id === t.id)
            .map((x) => branch(x, d + 1))
            .join("")
        : "")
    );
  }
  return arr
    .filter((t) => !t.parent_id || !ids.has(t.parent_id))
    .map((t) => branch(t, 0))
    .join("");
}
// Наборы хранятся по списку: «выбрать все» учитывает и свёрнутые узлы дерева.
const listChoices = {};
function pbar(list, arr, hideToday = false) {
  if (!opened[list]) return "";
  return `<div class="pbar"><label><input type="checkbox" class="cb" ${arr.length && arr.every((t) => (checks[list] || []).includes(t.id)) ? "checked" : ""} onchange="selectAll('${list}',this.checked)"> Все</label><b>Выбрано: ${(checks[list] || []).length}</b><span class="sp"></span><span>Перенести на:</span>${hideToday ? "" : button("Сегодня", `transfer('${list}','${today()}')`, "btn sm")}${button("Завтра", `transfer('${list}','${addDays(1)}')`, "btn sm")}${button("Через неделю", `transfer('${list}','${addDays(7)}')`, "btn sm")}<input type="date" aria-label="На дату" onchange="transfer('${list}',this.value)">${button("✕", `toggleList('${list}')`, "btn sm")}</div>`;
}
function listBlock(title, arr, list, opts = {}) {
  listChoices[list] = arr.map((t) => t.id);
  return `<section class="card ${opts.widget ? "widget" : "list"}"><div class="${opts.widget ? "whead" : "lhead"}">${opts.icon ? `<span class="wicon" style="background:${opts.icon === "today" ? "var(--accent-soft)" : opts.icon === "late" ? "var(--warn-soft)" : "#F0F0EC"};color:${opts.icon === "today" ? "var(--accent)" : opts.icon === "late" ? "var(--warn)" : "var(--muted)"}">${icon(opts.icon)}</span>` : ""}<div><h2>${esc(title)}</h2>${opts.sub ? `<div class="sub-n">${esc(opts.sub)}</div>` : ""}</div>${opts.progress != null ? `<div class="progress" style="max-width:120px;margin-left:8px"><i style="width:${opts.progress}%"></i></div>` : ""}<div class="sp"></div>${button("⧗ Прокрастинация " + (opened[list] ? "⌄" : "›"), `toggleList('${list}')`, "ptog " + (opened[list] ? "on" : ""))}${opts.extra || ""}</div>${pbar(list, arr, opts.hideToday)}${opts.stats ? stats(arr) : ""}<div class="rows">${opts.tree ? treeRows(arr, list) : arr.map((t) => row(t, list)).join("")}</div>${opts.footer ? `<div class="wfoot"><a data-go href="${opts.footer}">Открыть полный список →</a></div>` : ""}</section>`;
}
function toggleList(id) {
  opened[id] = !opened[id];
  checks[id] = [];
  render();
}
function toggleTree(id) {
  opened[id] = !opened[id];
  render();
}
function pick(el) {
  let a = checks[el.dataset.list] || [];
  checks[el.dataset.list] = el.checked
    ? [...a, el.value]
    : a.filter((x) => x !== el.value);
  render();
}
function selectAll(id, on) {
  checks[id] = on ? [...(listChoices[id] || [])] : [];
  render();
}
async function cycle(id) {
  let t = tasks.find((x) => x.id === id);
  t = {
    ...t,
    status: { waiting: "in_progress", in_progress: "done", done: "waiting" }[
      t.status
    ],
  };
  try {
    await api("/tasks/" + id, "PUT", t);
    await refresh();
    render();
  } catch (e) {
    alert(e.message);
  }
}
function confirmDialog(title, content, action) {
  let d = document.createElement("dialog");
  d.className = "dialog";
  d.innerHTML = `<h2>${esc(title)}</h2>${content}<div class="actions"><button class="btn cancel">Отмена</button><button class="btn pri confirm">Подтвердить</button></div>`;
  document.body.append(d);
  d.querySelector(".cancel").onclick = () => d.close();
  d.querySelector(".confirm").onclick = async () => {
    try {
      await action();
      d.close();
    } catch (e) {
      alert(e.message);
      d.close();
    }
  };
  d.onclose = () => d.remove();
  d.showModal();
}
// Подтверждение показывает весь выбранный набор; сервер повторно проверяет владение в одной транзакции.
function transfer(id, date) {
  let ids = checks[id] || [];
  if (!date || !ids.length) {
    alert("Выберите задачи и дату");
    return;
  }
  let chosen = ids.map((i) => tasks.find((t) => t.id === i)).filter(Boolean);
  confirmDialog(
    "Перенести задачи",
    `<p>На ${esc(date)}:</p><ul>${chosen.map((t) => `<li>${esc(t.title)}</li>`).join("")}</ul>`,
    async () => {
      await api("/tasks/bulk-due", "POST", { ids, due_date: date });
      checks[id] = [];
      await refresh();
      render();
    },
  );
}
function home() {
  let due = tasks.filter((t) => t.due_date === today()),
    plan = [...due, ...tasks.filter((t) => !t.due_date && t.status !== "done")],
    late = tasks
      .filter(overdue)
      .sort((a, b) => a.due_date.localeCompare(b.due_date)),
    tomorrow = tasks.filter((t) => t.due_date === addDays(1));
  let nearest = [...tasks].sort((a, b) =>
    (a.due_date || "9999").localeCompare(b.due_date || "9999"),
  );
  return `<div class="head"><div><h1>Добрый день</h1><div class="sub">${new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: "Europe/Moscow" })} · на сегодня ${due.length} задач, ${due.filter((t) => t.status === "done").length} исполнены</div></div><div class="sp"></div><label class="input quick"><input id="quick" placeholder="Быстро добавить задачу на сегодня…" onkeydown="quick(event)"></label></div><div class="grid2">${listBlock("План на сегодня", plan, "home-today", { widget: true, icon: "today", progress: due.length ? Math.round((100 * due.filter((t) => t.status === "done").length) / due.length) : 0, hideToday: true, sub: `${due.filter((t) => t.status === "done").length} из ${due.length} исполнено`, footer: "/tasks?date=today" })}${listBlock("Просрочено", late, "home-late", { widget: true, icon: "late", sub: `${late.length} задач`, footer: "/tasks?date=overdue" })}${listBlock("Задачи на завтра", tomorrow, "home-tomorrow", { widget: true, icon: "tomorrow", sub: `${tomorrow.length} задач`, footer: "/tasks?date=tomorrow" })}${listBlock("Все задачи", nearest, "home-all", { widget: true, icon: "all", stats: true, footer: "/tasks" })}</div>`;
}
async function quick(e) {
  if (e.key === "Enter" && e.target.value.trim()) {
    try {
      await api("/tasks", "POST", { title: e.target.value, due_date: today() });
      await refresh();
      render();
    } catch (err) {
      alert(err.message);
    }
  }
}
function filtered(base) {
  let a = base.filter(
    (t) =>
      t.title.toLowerCase().includes(query.toLowerCase()) &&
      (!statusFilter || t.status === statusFilter) &&
      (!projectFilter ||
        (projectFilter === "none"
          ? !t.project_id
          : t.project_id === projectFilter)) &&
      (!dateFilter ||
        (dateFilter === "overdue"
          ? overdue(t)
          : dateFilter === "today"
            ? t.due_date === today()
            : dateFilter === "tomorrow"
              ? t.due_date === addDays(1)
              : dateFilter === "week"
                ? t.due_date >= today() && t.due_date <= addDays(7)
                : !t.due_date)),
  );
  return a.sort((a, b) =>
    sort === "name"
      ? a.title.localeCompare(b.title, "ru")
      : sort === "desc"
        ? (b.due_date || "0000").localeCompare(a.due_date || "0000")
        : (a.due_date || "9999").localeCompare(b.due_date || "9999"),
  );
}
function filters(noProject) {
  let base = noProject ? tasks.filter((t) => !t.project_id) : tasks;
  let choices = [
    ["", "Все"],
    ["overdue", "Просрочено"],
    ["today", "Сегодня"],
    ["tomorrow", "Завтра"],
    ["week", "Неделя"],
    ["none", "Без даты"],
  ];
  let count = (v) =>
    base.filter((t) =>
      !v
        ? true
        : v === "overdue"
          ? overdue(t)
          : v === "today"
            ? t.due_date === today()
            : v === "tomorrow"
              ? t.due_date === addDays(1)
              : v === "week"
                ? t.due_date >= today() && t.due_date <= addDays(7)
                : !t.due_date,
    ).length;
  return `<div class="toolbar"><label class="input">⌕ <input placeholder="Поиск по названию" value="${esc(query)}" oninput="query=this.value;renderKeep(this)"></label><select class="select" aria-label="Статус" onchange="statusFilter=this.value;render()"><option value="">Все статусы</option>${Object.entries(
    statuses,
  )
    .map(
      ([k, v]) =>
        `<option value="${k}" ${statusFilter === k ? "selected" : ""}>${v}</option>`,
    )
    .join(
      "",
    )}</select>${noProject ? "" : `<select class="select" aria-label="Проект" onchange="projectFilter=this.value;render()"><option value="">Все проекты</option><option value="none" ${projectFilter === "none" ? "selected" : ""}>Без проекта</option>${projects.map((p) => `<option value="${p.id}" ${projectFilter === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>`}<select class="select" aria-label="Сортировка" onchange="sort=this.value;render()"><option value="due" ${sort === "due" ? "selected" : ""}>По сроку ↑</option><option value="desc" ${sort === "desc" ? "selected" : ""}>По сроку ↓</option><option value="name" ${sort === "name" ? "selected" : ""}>По названию</option></select></div><div class="seg dates" aria-label="Срок">${choices.map(([v, n]) => `<button class="${dateFilter === v ? "on" : ""}" onclick="dateFilter='${v}';render()">${n} <span class="n">${count(v)}</span></button>`).join("")}</div>`;
}
function renderKeep(el) {
  let pos = el.selectionStart;
  render();
  let target = document.querySelector('[placeholder="Поиск по названию"]');
  target?.focus();
  target?.setSelectionRange(pos, pos);
}
function taskList(noProject) {
  let a = filtered(noProject ? tasks.filter((t) => !t.project_id) : tasks);
  return `<div class="head"><div><h1>${noProject ? "Внепроектные задачи" : "Все задачи"}</h1><div class="sub">${a.length} задач</div></div><div class="sp"></div><div class="seg"><button class="${view === "list" ? "on" : ""}" onclick="view='list';render()">Список</button><button class="${view === "tree" ? "on" : ""}" onclick="view='tree';render()">Дерево</button></div></div>${filters(noProject)}<div class="split">${listBlock(
    "Задачи",
    a,
    "tasks",
    {
      tree: view === "tree",
      extra: !noProject
        ? `<div class="list-counts" aria-label="Количество задач по статусам">${Object.entries(
            statuses,
          )
            .map(
              ([key, label]) =>
                `<span title="${label}">${label}: ${a.filter((t) => t.status === key).length}</span>`,
            )
            .join("")}</div>`
        : "",
    },
  )}${noProject ? `<aside class="card peek panel"><h2>Быстрый просмотр</h2>${selected && tasks.find((t) => t.id === selected) ? peek(tasks.find((t) => t.id === selected)) : "Выберите задачу, чтобы открыть её карточку."}</aside>` : ""}</div>`;
}
function peek(t) {
  let days = overdue(t)
    ? Math.floor((new Date(today()) - new Date(t.due_date)) / 86400000)
    : 0;
  return `<b>${esc(t.title)}</b><p>Статус: ${statuses[t.status]}</p><p>Проект: ${esc(proj(t.project_id)?.name || "Без проекта")}</p><p>Срок: ${dateText(t)} ${days ? "· просрочено на " + days + " дн." : ""}</p><p>Метки: ${t.labels.map(esc).join(", ") || "Нет"}</p><p>${esc(t.description.slice(0, 180))}</p><a data-go class="btn" href="/tasks/${t.id}">Открыть</a> <a data-go class="btn" href="/tasks/${t.id}/edit">Редактировать</a>`;
}
function projectPage(id) {
  let p = projects.find((x) => x.id === id) || projects[0];
  let arr = p ? tasks.filter((t) => t.project_id === p.id) : [];
  return `<div class="head"><div><h1>Проекты</h1><div class="sub">Все ваши проекты в одном месте</div></div><div class="sp"></div>${button("＋ Создать новый проект", "projectForm()", "btn pri")}</div><div class="card project-layout"><aside class="plist"><label class="input">⌕ <input placeholder="Поиск проектов" oninput="document.querySelectorAll('.plist a').forEach(x=>x.hidden=!x.textContent.toLowerCase().includes(this.value.toLowerCase()))"></label>${projects.map((x) => `<a data-go class="${p?.id === x.id ? "on" : ""}" href="/projects/${x.id}"><span class="dot" style="background:${esc(x.color)}"></span><span>${esc(x.name)}</span><span class="c">${tasks.filter((t) => t.project_id === x.id).length}</span></a>`).join("")}</aside><div class="pbody">${p ? `<div class="head"><div><h1>${esc(p.name)}</h1><div class="sub project-description md">${p.description_html || ""}</div><div class="tags project-labels">${p.labels.map((label) => `<span class="chip">${esc(label)}</span>`).join("")}</div></div><div class="sp"></div>${button("Изменить", `projectForm('${p.id}')`)}</div>${stats(arr, true)}${listBlock("Задачи · сначала новые", arr, "project", { tree: true, extra: `<a data-go class="btn pri" href="/tasks/new?project=${p.id}">＋ Задача в проект</a>` })}` : "<div>Создайте первый проект</div>"}</div></div>`;
}
// Форма проекта содержит собственное состояние. Цвет фиксируется в истории
// только после успешного ответа сервера, поэтому отмена не меняет историю.
async function projectForm(id = "") {
  const project = projects.find((x) => x.id === id) || {
    name: "",
    description: "",
    description_format: "text",
    labels: [],
    color: "#3451D1",
  };
  let recent;
  try {
    recent = await api("/project-colors");
  } catch (error) {
    alert(error.message);
    return;
  }
  const presets = [
    "#3451D1",
    "#1D7A4C",
    "#8A4FD8",
    "#B23F0B",
    "#C42C57",
    "#007F91",
    "#D27600",
    "#6952A3",
    "#A03385",
    "#526B18",
  ];
  const dialog = document.createElement("dialog");
  dialog.className = "dialog project-dialog";
  dialog.innerHTML = `
    <form id="project-form" novalidate>
      <div class="project-dialog-body">
        <h2>${id ? "Изменить проект" : "Новый проект"}</h2>
        <div class="form project-form-fields">
          <div class="field full"><label for="project-name">Название проекта</label><input class="input title-in" id="project-name" name="name" required maxlength="200" value="${esc(project.name)}" placeholder="Название проекта"></div>
          <div class="field full"><span class="lbl">Описание</span>
            <div class="seg" aria-label="Формат описания проекта"><button type="button" data-project-format="text">Текст</button><button type="button" data-project-format="markdown">Markdown</button></div>
            <div class="editor"><textarea name="description" aria-label="Описание проекта" maxlength="20000" placeholder="Описание проекта">${esc(project.description)}</textarea><div class="editor-preview md" aria-label="Предпросмотр описания проекта" hidden></div></div>
          </div>
          <div class="field full"><label for="project-label-input">Метки</label><div class="tagbox project-tags"></div><input id="project-label-input" class="input" list="project-label-options" placeholder="Метка, Enter — добавить" autocomplete="off"><datalist id="project-label-options">${[...new Set(projects.flatMap((p) => p.labels))].map((label) => `<option value="${esc(label)}"></option>`).join("")}</datalist></div>
          <div class="field full"><span class="lbl">Цвет проекта</span>
            <div class="color-picker">
              <div class="seg" aria-label="Способ выбора цвета"><button type="button" data-color-tab="palette" class="on">Палитра</button><button type="button" data-color-tab="rgb">RGB</button></div>
              <div class="color-palette" role="group" aria-label="Палитра цветов">${presets.map((color) => `<button type="button" class="color-swatch" data-color="${color}" style="--swatch:${color}" aria-label="${color}" title="${color}"></button>`).join("")}</div>
              <div class="color-rgb" hidden><label>R<input type="number" name="red" min="0" max="255" step="1" inputmode="numeric"></label><label>G<input type="number" name="green" min="0" max="255" step="1" inputmode="numeric"></label><label>B<input type="number" name="blue" min="0" max="255" step="1" inputmode="numeric"></label><label>HEX<input name="hex" aria-label="HEX" maxlength="7" pattern="#[0-9A-Fa-f]{6}"></label><span class="color-preview" aria-label="Выбранный цвет"></span></div>
              <div class="color-recent-title">Последние цвета</div><div class="color-recent" aria-label="Последние цвета">${Array.from({ length: 20 }, (_, i) => (recent[i] ? `<button type="button" class="color-swatch" data-color="${esc(recent[i])}" style="--swatch:${esc(recent[i])}" aria-label="Последний цвет ${esc(recent[i])}" title="${esc(recent[i])}"></button>` : '<span class="color-empty" aria-hidden="true"></span>')).join("")}</div>
              <div class="selected-color"><span class="color-preview" aria-hidden="true"></span><span class="color-value"></span></div>
            </div>
          </div>
        </div>
        <p class="error" role="alert"></p>
      </div>
      <div class="foot"><span class="sp"></span><button type="button" class="btn cancel">Отмена</button><button class="btn pri">Сохранить</button></div>
    </form>`;
  document.body.append(dialog);
  const form = dialog.querySelector("form");
  let format = project.description_format,
    projectLabels = [...project.labels],
    color = project.color.toUpperCase(),
    version = 0;
  const input = (name) => form.elements.namedItem(name);
  const labelInput = dialog.querySelector("#project-label-input");
  function drawLabels() {
    const box = dialog.querySelector(".project-tags");
    box.replaceChildren();
    projectLabels.forEach((label, index) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.append(document.createTextNode(label + " "));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Удалить метку " + label);
      remove.onclick = () => {
        projectLabels.splice(index, 1);
        drawLabels();
      };
      chip.append(remove);
      box.append(chip);
    });
  }
  function addProjectLabel() {
    const value = labelInput.value.trim().slice(0, 100);
    if (value && !projectLabels.includes(value)) projectLabels.push(value);
    labelInput.value = "";
    drawLabels();
  }
  labelInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addProjectLabel();
    }
  });
  async function updatePreview() {
    const pane = dialog.querySelector(".editor-preview");
    pane.hidden = format !== "markdown";
    dialog
      .querySelectorAll("[data-project-format]")
      .forEach((button) =>
        button.classList.toggle("on", button.dataset.projectFormat === format),
      );
    const current = ++version;
    if (format !== "markdown") return;
    try {
      const result = await api("/markdown", "POST", {
        text: input("description").value,
      });
      if (current === version && dialog.isConnected)
        pane.innerHTML = result.html;
    } catch (error) {
      dialog.querySelector(".error").textContent = error.message;
    }
  }
  dialog.querySelectorAll("[data-project-format]").forEach(
    (button) =>
      (button.onclick = () => {
        format = button.dataset.projectFormat;
        updatePreview();
      }),
  );
  input("description").addEventListener("input", updatePreview);
  function setColor(value) {
    color = value.toUpperCase();
    ["red", "green", "blue"].forEach((name, index) => {
      input(name).value = parseInt(
        color.slice(1 + index * 2, 3 + index * 2),
        16,
      );
    });
    input("hex").value = color;
    dialog
      .querySelectorAll(".color-preview")
      .forEach((preview) => (preview.style.backgroundColor = color));
    dialog.querySelector(".color-value").textContent = color;
    dialog
      .querySelectorAll("[data-color]")
      .forEach((swatch) =>
        swatch.classList.toggle("selected", swatch.dataset.color === color),
      );
  }
  dialog
    .querySelectorAll("[data-color]")
    .forEach(
      (swatch) => (swatch.onclick = () => setColor(swatch.dataset.color)),
    );
  dialog.querySelectorAll("[data-color-tab]").forEach(
    (button) =>
      (button.onclick = () => {
        const rgb = button.dataset.colorTab === "rgb";
        dialog.querySelector(".color-palette").hidden = rgb;
        dialog.querySelector(".color-rgb").hidden = !rgb;
        dialog
          .querySelectorAll("[data-color-tab]")
          .forEach((tab) => tab.classList.toggle("on", tab === button));
      }),
  );
  ["red", "green", "blue"].forEach((name) =>
    input(name).addEventListener("input", () => {
      const components = ["red", "green", "blue"].map((key) => input(key));
      if (
        components.every(
          (element) => element.value !== "" && element.validity.valid,
        )
      ) {
        setColor(
          "#" +
            components
              .map((element) =>
                Number(element.value).toString(16).padStart(2, "0"),
              )
              .join(""),
        );
      }
    }),
  );
  input("hex").addEventListener("input", () => {
    if (/^#[0-9a-f]{6}$/i.test(input("hex").value))
      setColor(input("hex").value);
  });
  dialog.querySelector(".cancel").onclick = () => dialog.close();
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (!input("name").value.trim()) {
      dialog.querySelector(".error").textContent = "Введите название проекта";
      input("name").focus();
      return;
    }
    // Проверяем и скрытые RGB-поля: нельзя сохранить устаревший цвет после неверного ввода.
    if (
      !["red", "green", "blue", "hex"].every(
        (name) => input(name).value !== "" && input(name).validity.valid,
      )
    ) {
      dialog.querySelector(".error").textContent =
        "Введите корректные значения RGB и HEX";
      return;
    }
    if (labelInput.value.trim()) addProjectLabel();
    try {
      const saved = await api(
        "/projects" + (id ? "/" + id : ""),
        id ? "PUT" : "POST",
        {
          name: input("name").value,
          description: input("description").value,
          description_format: format,
          labels: projectLabels,
          color,
        },
      );
      dialog.close();
      await refresh();
      go("/projects/" + saved.id);
    } catch (error) {
      dialog.querySelector(".error").textContent = error.message;
    }
  };
  dialog.onclose = () => dialog.remove();
  drawLabels();
  setColor(color);
  dialog.showModal();
  updatePreview();
}
async function card(id) {
  let t = await api("/tasks/" + id),
    parent = tasks.find((x) => x.id === t.parent_id),
    children = tasks.filter((x) => x.parent_id === id);
  let desc =
    t.description_format === "markdown"
      ? (await api("/markdown", "POST", { text: t.description })).html
      : esc(t.description);
  let days = overdue(t)
    ? Math.floor((new Date(today()) - new Date(t.due_date)) / 86400000)
    : 0;
  let fmtDate = t.due_date
    ? new Date(t.due_date + "T12:00:00Z").toLocaleDateString("ru-RU", {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      })
    : "Без даты";
  return `<nav class="crumbs"><a data-go href="${t.project_id ? "/projects/" + t.project_id : "/tasks/no-project"}">${esc(proj(t.project_id)?.name || "Внепроектные задачи")}</a> › ${esc(t.title)}</nav><div class="split task-detail"><div class="card list"><div class="panel"><div class="task-heading"><div><h1>${esc(t.title)}</h1><div class="heading-chips"><span class="chip status-${t.status}">${statuses[t.status]}</span>${days ? `<span class="chip hot">Просрочено на ${days} дн.</span>` : ""}</div></div><div class="task-actions"><a data-go class="btn pri" href="/tasks/${id}/edit">Редактировать</a><details class="task-menu"><summary class="btn icon" aria-label="Действия">···</summary><div class="menu">${button("Дублировать", `duplicate('${id}')`, "mi")}${button("Удалить", `removeTask('${id}')`, "mi")}</div></details></div></div><div class="detail-section"><div class="lbl">Сменить статус</div><div class="seg">${Object.entries(
    statuses,
  )
    .map(
      ([k, v]) =>
        `<button class="${t.status === k ? "on" : ""}" onclick="setStatus('${id}','${k}')">${v}</button>`,
    )
    .join(
      "",
    )}</div></div><div class="detail-section kv"><div class="k">${icon("projects")} Проект</div><div class="v">${esc(proj(t.project_id)?.name || "Без проекта")}</div><div class="k">${icon("today")} Сделать до</div><div class="v ${days ? "error" : ""}">${esc(fmtDate)} ${days ? " · просрочено на " + days + " дн." : ""}</div><div class="k">${icon("all")} Родительская задача</div><div class="v">${parent ? `<a data-go href="/tasks/${parent.id}">${esc(parent.title)}</a>` : "Нет"}</div><div class="k">♧ Метки</div><div class="v">${t.labels.map((x) => `<span class="chip">${esc(x)}</span>`).join("") || "Нет"}</div></div><div class="detail-section"><h2>Описание</h2><div class="md description ${t.description_format === "markdown" ? "markdown" : ""}">${desc || "Нет описания"}</div></div></div><div class="subtask-section">${listBlock("Подзадачи", children, "subtasks", { sub: `${children.filter((x) => x.status === "done").length} из ${children.length} исполнено`, extra: `<a data-go class="btn" href="/tasks/new?parent=${id}">＋ Подзадача</a>` })}</div></div><aside class="card peek panel"><h2 class="sec">История</h2>${t.history.map((h) => `<div class="history-item"><b>${esc({ created: "Создана", updated: "Редактирование", edited: "Редактирование", status_changed: "Смена статуса", due_changed: "Перенос срока", subtask_done: "Подзадача исполнена" }[h.type] || h.type)}</b><div class="sub-n">${new Date(h.at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })}</div>${h.data.title ? esc(h.data.title) : ""}${Object.hasOwn(h.data, "from") ? `<div class="sub-n">${esc(statuses[h.data.from] || h.data.from || "Без даты")} → ${esc(statuses[h.data.to] || h.data.to || "Без даты")}</div>` : ""}</div>`).join("")}</aside></div>`;
}
async function setStatus(id, status) {
  let t = tasks.find((x) => x.id === id);
  try {
    await api("/tasks/" + id, "PUT", { ...t, status });
    await refresh();
    render();
  } catch (e) {
    alert(e.message);
  }
}
async function duplicate(id) {
  try {
    let t = await api("/tasks/" + id + "/duplicate", "POST", {});
    await refresh();
    go("/tasks/" + t.id);
  } catch (e) {
    alert(e.message);
  }
}
async function removeTask(id) {
  try {
    let arr = await api("/tasks/" + id + "/delete-preview");
    confirmDialog(
      "Удалить задачу и подзадачи?",
      `<p>Будут удалены все перечисленные задачи:</p><ul>${arr.map((t) => `<li>${esc(t.title)}</li>`).join("")}</ul>`,
      async () => {
        await api("/tasks/" + id + "/delete", "POST", {
          ids: arr.map((t) => t.id),
        });
        await refresh();
        go("/tasks");
      },
    );
  } catch (e) {
    alert(e.message);
  }
}
function taskForm(id) {
  let params = new URLSearchParams(location.search),
    old = tasks.find((t) => t.id === id),
    parent = tasks.find((t) => t.id === params.get("parent"));
  let t = old || {
    title: "",
    status: "waiting",
    description: "",
    description_format: "text",
    parent_id: parent?.id || null,
    project_id: parent?.project_id || params.get("project"),
    due_date: "",
    labels: [],
  };
  formStatus = t.status;
  formFormat = t.description_format;
  labels = [...t.labels];
  return `<nav class="crumbs"><a data-go href="/">Главная</a> › ${id ? "Редактирование" : "Новая задача"}</nav><div class="head"><h1>${id ? "Редактирование задачи" : "Новая задача"}</h1></div><form class="card" id="task-form" novalidate style="max-width:980px"><div class="form"><div class="field full"><label>Название задачи</label><label class="input title-in"><input name="title" required value="${esc(t.title)}" placeholder="Что нужно сделать?"></label><span class="error" id="form-error"></span></div><div class="field full"><span class="lbl">Статус</span><div class="seg">${Object.entries(
    statuses,
  )
    .map(
      ([k, v]) =>
        `<button type="button" data-status="${k}" class="${t.status === k ? "on" : ""}" onclick="setFormStatus(this)">${v}</button>`,
    )
    .join(
      "",
    )}</div></div><div class="field full"><span class="lbl">Описание</span><div class="seg"><button type="button" data-format="text" class="${t.description_format === "text" ? "on" : ""}" onclick="setFormat(this)">Текст</button><button type="button" data-format="markdown" class="${t.description_format === "markdown" ? "on" : ""}" onclick="setFormat(this)">Markdown</button></div><div class="editor"><textarea name="description" placeholder="Подробности, ссылки, чек-лист…" oninput="preview()">${esc(t.description)}</textarea><div class="editor-preview" ${t.description_format === "text" ? "hidden" : ""}></div></div></div><div class="field"><label>Родительская задача</label><div class="dd"><input type="hidden" name="parent_id" value="${esc(t.parent_id || "")}"><input class="input" role="combobox" aria-label="Родительская задача" autocomplete="off" data-combo="parent" value="${esc(tasks.find((x) => x.id === t.parent_id)?.title || "")}" placeholder="Поиск задачи…" oninput="comboInput(this)" onfocus="comboInput(this,false)" onkeydown="comboKey(event)"><div class="menu combo-menu" hidden></div></div></div><div class="field"><label>Проект</label><div class="dd"><input type="hidden" name="project_id" value="${esc(t.project_id || "")}"><input class="input" role="combobox" aria-label="Проект" autocomplete="off" data-combo="project" value="${esc(proj(t.project_id)?.name || "")}" placeholder="Поиск проекта…" oninput="comboInput(this)" onfocus="comboInput(this,false)" onkeydown="comboKey(event)"><div class="menu combo-menu" hidden></div></div></div><div class="field"><label>Сделать до</label><input class="input" type="date" name="due_date" value="${esc(t.due_date || "")}"><div>${button("Сегодня", `setDate('${today()}')`, "btn sm")} ${button("Завтра", `setDate('${addDays(1)}')`, "btn sm")} ${button("Через неделю", `setDate('${addDays(7)}')`, "btn sm")} ${button("✕", `setDate('')`, "btn sm")}</div></div><div class="field"><label>Метки</label><div class="tagbox" id="tags"></div><input class="input" list="label-options" id="tag-input" placeholder="Метка, Enter — добавить" onkeydown="tagKey(event)"><datalist id="label-options">${[...new Set(tasks.flatMap((x) => x.labels))].map((x) => `<option value="${esc(x)}">`).join("")}</datalist><div class="sub-n">Часто: ${[
    ...new Set(tasks.flatMap((x) => x.labels)),
  ]
    .slice(0, 8)
    .map(
      (x) =>
        `<button type="button" class="chip" onclick="addTag(this.textContent)">${esc(x)}</button>`,
    )
    .join(
      " ",
    )}</div></div></div><div class="foot">${id ? button("Удалить задачу", `removeTask('${id}')`, "btn danger") : ""}<span class="sp"></span><a data-go class="btn" href="${id ? "/tasks/" + id : "/"}">Отмена</a><button class="btn pri">${id ? "Сохранить" : "Создать задачу"}</button></div></form>`;
}
function setFormStatus(el) {
  formStatus = el.dataset.status;
  document
    .querySelectorAll("[data-status]")
    .forEach((b) => b.classList.toggle("on", b === el));
}
function setFormat(el) {
  formFormat = el.dataset.format;
  document
    .querySelectorAll("[data-format]")
    .forEach((b) => b.classList.toggle("on", b === el));
  $(".editor-preview").hidden = formFormat === "text";
  preview();
}
let previewVersion = 0;
async function preview() {
  if (formFormat !== "markdown") return;
  let version = ++previewVersion;
  try {
    let d = await api("/markdown", "POST", {
      text: $("[name=description]").value,
    });
    if (version === previewVersion && $(".editor-preview"))
      $(".editor-preview").innerHTML = d.html;
  } catch (e) {
    console.error(e);
  }
}
function setDate(v) {
  $("[name=due_date]").value = v;
}
function updateTags() {
  $("#tags").innerHTML = labels
    .map(
      (l, i) =>
        `<span class="chip">${esc(l)} <button type="button" onclick="removeTag(${i})">×</button></span>`,
    )
    .join("");
}
function addTag(s) {
  s = s.trim();
  if (s && !labels.includes(s)) labels.push(s);
  updateTags();
}
function removeTag(i) {
  labels.splice(i, 1);
  updateTags();
}
function tagKey(e) {
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    addTag(e.target.value);
    e.target.value = "";
  }
}
// Комбобокс хранит идентификатор отдельно от видимого названия: совпадающие названия безопасны.
let comboItems = [],
  comboIndex = 0;
function comboInput(input, clear = true) {
  document.querySelectorAll(".combo-menu").forEach((m) => {
    if (m !== input.parentElement.querySelector(".combo-menu")) m.hidden = true;
  });
  let kind = input.dataset.combo,
    hidden = input.parentElement.querySelector("input[type=hidden]");
  if (clear) hidden.value = "";
  let term = input.value.trim().toLocaleLowerCase("ru"),
    current = location.pathname.split("/")[2];
  let descendants = new Set([current]);
  let changed = true;
  while (changed) {
    changed = false;
    for (let t of tasks)
      if (
        t.parent_id &&
        descendants.has(t.parent_id) &&
        !descendants.has(t.id)
      ) {
        descendants.add(t.id);
        changed = true;
      }
  }
  let project = $("[name=project_id]").value;
  comboItems = (
    kind === "project"
      ? projects.map((p) => ({ id: p.id, name: p.name, detail: "" }))
      : tasks
          .filter(
            (t) => !descendants.has(t.id) && t.project_id === (project || null),
          )
          .map((t) => ({
            id: t.id,
            name: t.title,
            detail: proj(t.project_id)?.name || "Без проекта",
          }))
  ).filter((x) => x.name.toLocaleLowerCase("ru").includes(term));
  if (
    kind === "project" &&
    term &&
    !comboItems.some((x) => x.name.toLocaleLowerCase("ru") === term)
  )
    comboItems.push({
      id: "__new__",
      name: input.value.trim(),
      detail: "Создать проект",
    });
  comboIndex = 0;
  drawCombo(input);
}
function drawCombo(input) {
  let menu = input.parentElement.querySelector(".combo-menu");
  menu.hidden = false;
  menu.innerHTML = comboItems.length
    ? comboItems
        .map((x, i) => {
          let label = esc(x.name),
            term = input.value.trim();
          if (term && x.id !== "__new__") {
            let pos = x.name
              .toLocaleLowerCase("ru")
              .indexOf(term.toLocaleLowerCase("ru"));
            if (pos >= 0)
              label =
                esc(x.name.slice(0, pos)) +
                "<mark>" +
                esc(x.name.slice(pos, pos + term.length)) +
                "</mark>" +
                esc(x.name.slice(pos + term.length));
          }
          return `<div role="option" class="mi ${i === comboIndex ? "hl" : ""}" onmousedown="event.preventDefault();chooseCombo(this.closest('.dd').querySelector('[data-combo]'),${i})">${x.id === "__new__" ? "＋ Создать проект «" + label + "»" : label + (x.detail ? " <small> · " + esc(x.detail) + "</small>" : "")}</div>`;
        })
        .join("")
    : '<div class="hint">Ничего не найдено</div>';
}
function comboKey(e) {
  let menu = e.target.parentElement.querySelector(".combo-menu");
  if (e.key === "Escape") {
    menu.hidden = true;
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (menu.hidden) comboInput(e.target, false);
    else {
      comboIndex = Math.max(
        0,
        Math.min(
          comboItems.length - 1,
          comboIndex + (e.key === "ArrowDown" ? 1 : -1),
        ),
      );
      drawCombo(e.target);
    }
  } else if (e.key === "Enter" && !menu.hidden && comboItems.length) {
    e.preventDefault();
    chooseCombo(e.target, comboIndex);
  }
}
async function chooseCombo(input, index) {
  let item = comboItems[index];
  if (!item) return;
  if (item.id === "__new__") {
    try {
      item = await api("/projects", "POST", { name: item.name });
      projects.push(item);
    } catch (e) {
      alert(e.message);
      return;
    }
  }
  let hidden = input.parentElement.querySelector("input[type=hidden]");
  hidden.value = item.id;
  input.value = item.name;
  input.parentElement.querySelector(".combo-menu").hidden = true;
  if (input.dataset.combo === "project") {
    let parent = $("[name=parent_id]"),
      selected = tasks.find((t) => t.id === parent.value);
    if (selected && selected.project_id !== item.id) {
      parent.value = "";
      $("[data-combo=parent]").value = "";
    }
  }
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".dd"))
    document.querySelectorAll(".combo-menu").forEach((x) => (x.hidden = true));
});
async function submitTask(e, id) {
  e.preventDefault();
  let f = new FormData(e.target),
    title = f.get("title").trim();
  if (!title) {
    $("#form-error").textContent = "Введите название";
    return;
  }
  let p = projects.find((x) => x.id === f.get("project_id")),
    par = tasks.find((x) => x.id === f.get("parent_id"));
  if (
    ($("[data-combo=project]").value && !p) ||
    ($("[data-combo=parent]").value && !par)
  ) {
    $("#form-error").textContent = "Выберите значение из списка";
    return;
  }
  try {
    let t = await api("/tasks" + (id ? "/" + id : ""), id ? "PUT" : "POST", {
      title,
      status: formStatus,
      description: f.get("description"),
      description_format: formFormat,
      parent_id: par?.id || null,
      project_id: p?.id || null,
      due_date: f.get("due_date") || null,
      labels,
    });
    await refresh();
    go("/tasks/" + t.id);
  } catch (err) {
    $("#form-error").textContent = err.message;
  }
}
// Счётчик отсеивает устаревший ответ асинхронной карточки после перехода на другой экран.
let renderVersion = 0;
async function render() {
  let version = ++renderVersion;
  let root = $("#root");
  if (!me) {
    root.innerHTML = loginPage();
    $("#login").onsubmit = loginSubmit;
    $("#login [name=username]").onchange = checkCaptcha;
    return;
  }
  let path = location.pathname.split("/").filter(Boolean);
  let html;
  if (!path.length) html = home();
  else if (path[0] === "projects") html = projectPage(path[1]);
  else if (path[0] === "tasks" && path[1] === "new") html = taskForm();
  else if (path[0] === "tasks" && path[1] && path[1] !== "no-project") {
    try {
      html = path[2] === "edit" ? taskForm(path[1]) : await card(path[1]);
    } catch (e) {
      html = `<h1>${esc(e.message)}</h1>`;
    }
  } else html = taskList(path[1] === "no-project");
  if (version !== renderVersion) return;
  root.innerHTML = shell(html, !path.length);
  document.querySelectorAll(".nav a[data-go]").forEach((a) => {
    let href = a.getAttribute("href");
    if (href === location.pathname) a.classList.add("on");
  });
  if ($("#task-form")) {
    updateTags();
    preview();
    $("#task-form").onsubmit = (e) =>
      submitTask(e, path[1] === "new" ? null : path[1]);
    $("#task-form").onkeydown = (e) => {
      if (e.ctrlKey && e.key === "Enter") e.target.form.requestSubmit();
    };
  }
}
(async () => {
  try {
    me = await api("/me");
    csrf = me.csrf;
    await refresh();
  } catch (e) {
    me = null;
  }
  render();
})();
