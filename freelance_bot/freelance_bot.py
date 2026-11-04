import html
import json
import os
import sys
import time
from pathlib import Path

import requests

TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]
FH_TOKEN = os.environ["FH_TOKEN"]

SKILL_IDS_OVERRIDE = os.environ.get("SKILL_IDS", "")

POLL_INTERVAL = 60
FH_API = "https://api.freelancehunt.com/v2"
SEEN_FILE = Path("seen_projects.json")

fh = requests.Session()
fh.headers.update({
    "Authorization": f"Bearer {FH_TOKEN}",
    "Accept": "application/json",
    "Accept-Language": "ru",
})


def fh_get(path, params=None):
    r = fh.get(f"{FH_API}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_my_skill_ids():
    if SKILL_IDS_OVERRIDE.strip():
        return [int(x) for x in SKILL_IDS_OVERRIDE.split(",") if x.strip()]

    data = fh_get("/my/profile")
    attrs = data.get("data", {}).get("attributes", {})
    skills = attrs.get("skills") or []
    ids = [s["id"] for s in skills if isinstance(s, dict) and "id" in s]
    if not ids:
        sys.exit(
            "Не удалось получить навыки из профиля. Укажите их вручную через "
            "переменную SKILL_IDS (узнать id: python freelance_bot.py skills)."
        )
    return ids


def fetch_projects(skill_ids):
    data = fh_get("/projects", params={"filter[skill_id]": ",".join(map(str, skill_ids))})
    return data.get("data", [])


def format_budget(attrs):
    b = attrs.get("budget")
    if isinstance(b, dict) and b.get("amount"):
        return f"{b['amount']} {b.get('currency', '')}".strip()
    return "не указан"


def build_message(project):
    a = project.get("attributes", {})
    title = html.escape(a.get("name", "Без названия"))
    desc = html.escape((a.get("description") or "").strip())
    link = (project.get("links", {}).get("self", {}) or {}).get("web") or \
        f"https://freelancehunt.com/project/{project.get('id')}.html"
    head = f"<b>{title}</b>\n💰 Бюджет: {format_budget(a)}\n🔗 {link}\n\n"
    return head, desc


def tg_send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not r.ok:
        print("Telegram error:", r.status_code)  # тело ответа не печатаем: логи публичные
    return r.ok


def send_project(project):
    head, desc = build_message(project)
    limit = 4000
    size = limit - len(head)
    chunks = [desc[i:i + size] for i in range(0, max(len(desc), 1), size)]
    ok = tg_send(head + chunks[0])
    for chunk in chunks[1:]:
        ok = tg_send(chunk) and ok
        time.sleep(0.5)
    return ok


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return None


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def run_cycle(skill_ids, seen):
    """Один проход: берём заказы, отправляем новые, сохраняем состояние."""
    projects = fetch_projects(skill_ids)
    new = [p for p in projects if p["id"] not in seen]
    for p in reversed(new):
        if send_project(p):
            seen.add(p["id"])
        time.sleep(1)
    if new:
        save_seen(seen)


def print_skills(query=""):
    data = fh_get("/skills")
    q = query.lower().strip()
    for s in data.get("data", []):
        name = s.get("attributes", {}).get("name") or s.get("name") or ""
        if q in name.lower():
            print(s["id"], "-", name)


def print_profile():
    data = fh_get("/my/profile")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def debug_projects():
    skill_ids = get_my_skill_ids()
    all_p = fh_get("/projects").get("data", [])
    filt = fetch_projects(skill_ids)
    print("skill_ids:", skill_ids)
    print("без фильтра:", [p["id"] for p in all_p])
    print("с фильтром: ", [p["id"] for p in filt])
    if filt:
        print(json.dumps(filt[0], ensure_ascii=False, indent=2))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "skills":
        print_skills(" ".join(sys.argv[2:]))
        return
    if cmd == "profile":
        print_profile()
        return
    if cmd == "debug":
        debug_projects()
        return

    skill_ids = get_my_skill_ids()
    seen = load_seen()

    if seen is None:
        # первый запуск: запоминаем текущие заказы, чтобы не завалить чат старыми
        seen = {p["id"] for p in fetch_projects(skill_ids)}
        save_seen(seen)
        tg_send("✅ Бот запущен. Буду присылать новые заказы по вашей специализации.")
        if "--once" in sys.argv:
            return

    if "--once" in sys.argv:
        # режим для GitHub Actions: один проход и выход
        run_cycle(skill_ids, seen)
        return

    print("Бот работает. Ctrl+C для остановки.")
    while True:
        try:
            run_cycle(skill_ids, seen)
        except requests.HTTPError as e:
            print("HTTP error:", e)
            if e.response is not None and e.response.status_code == 429:
                time.sleep(120)
        except Exception as e:
            print("Error:", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()