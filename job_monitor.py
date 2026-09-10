"""
自動監控 Studierendenwerk Heidelberg 線上職缺板，偵測是否有新職缺釋出，
並透過 Telegram Bot 推播通知。設計成可在 GitHub Actions 排程中執行。

原理：
    這個頁面的職缺清單是由「Search & Filter Pro」外掛用 JavaScript/AJAX
    動態載入的（class 為 searchandfilter--results__item），所以不能單純用
    requests 抓 HTML，必須用無頭瀏覽器（Playwright）等內容渲染完才能解析。

環境變數（在 GitHub Actions 裡用 Secrets 設定）：
    TELEGRAM_BOT_TOKEN  - 從 @BotFather 拿到的 bot token
    TELEGRAM_CHAT_ID    - 你要接收通知的 chat id

本機測試用法：
    export TELEGRAM_BOT_TOKEN=xxxx
    export TELEGRAM_CHAT_ID=xxxx
    pip install -r requirements.txt
    playwright install chromium
    python job_monitor.py
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

URL = "https://www.stw.uni-heidelberg.de/beratung-service/online-jobboerse/"
STATE_FILE = Path(__file__).parent / "job_state.json"
ITEM_SELECTOR = ".searchandfilter--results__item"


def fetch_current_jobs():
    """用無頭瀏覽器開啟職缺頁，回傳目前所有職缺的 dict（key 為職缺編號）。"""
    jobs = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        )
        page.goto(URL, wait_until="networkidle", timeout=60_000)

        # 等待職缺列表真的渲染出來（AJAX 載入完成）
        page.wait_for_selector(ITEM_SELECTOR, timeout=30_000)

        items = page.query_selector_all(ITEM_SELECTOR)

        for item in items:
            href = item.get_attribute("href") or ""

            nummer_el = item.query_selector(".searchandfilter--results__nummer")
            titel_el = item.query_selector(".searchandfilter--results__jobtitel")
            ort_el = item.query_selector(".searchandfilter--results__ort")
            datum_el = item.query_selector(".searchandfilter--results__datum")

            nummer = nummer_el.inner_text().strip() if nummer_el else None
            if not nummer:
                continue

            jobs[nummer] = {
                "titel": titel_el.inner_text().strip() if titel_el else "",
                "ort": ort_el.inner_text().strip() if ort_el else "",
                "datum": datum_el.inner_text().strip() if datum_el else "",
                "url": href,
            }

        browser.close()

    return jobs


def load_previous_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(jobs):
    STATE_FILE.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def send_telegram_message(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳過推播，僅印在 log 裡。")
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            api_url,
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"Telegram 發送失敗: {resp.status_code} {resp.text}", file=sys.stderr)
    except Exception as e:
        print(f"Telegram 發送發生例外: {e}", file=sys.stderr)


def notify_new_jobs(new_jobs: dict):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] 發現 {len(new_jobs)} 個新職缺：")

    lines = [f"🆕 <b>Heidelberg Jobbörse 有 {len(new_jobs)} 個新職缺！</b>", ""]

    for nummer, job in new_jobs.items():
        line_console = f"  - #{nummer} {job['titel']} @ {job['ort']} ({job['datum']})"
        print(line_console)
        print(f"    {job['url']}")

        lines.append(
            f"• <b>{job['titel']}</b>\n"
            f"  📍 {job['ort']} ｜ 🗓 {job['datum']}\n"
            f"  <a href=\"{job['url']}\">查看職缺 #{nummer}</a>"
        )
        lines.append("")

    send_telegram_message("\n".join(lines).strip())


def main():
    try:
        current_jobs = fetch_current_jobs()
    except Exception as e:
        print(f"抓取職缺頁失敗：{e}", file=sys.stderr)
        sys.exit(1)

    if not current_jobs:
        print("警告：這次沒抓到任何職缺，可能是網頁結構變了或載入逾時，先不更新狀態。")
        sys.exit(1)

    previous_jobs = load_previous_state()

    new_ids = set(current_jobs) - set(previous_jobs)

    if new_ids:
        new_jobs = {nid: current_jobs[nid] for nid in new_ids}
        notify_new_jobs(new_jobs)
    else:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] 沒有新職缺（目前共 {len(current_jobs)} 筆）。")

    save_state(current_jobs)


if __name__ == "__main__":
    main()
