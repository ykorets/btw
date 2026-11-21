"""btw_engine.notify — Telegram daily report + failure alerts (M8.1).

Runs as the last step of daily-intake (if: always()). On success sends the
day's digest: source health, fresh candidates with keyword hits (the
companies moving right now), staged facts awaiting review, LLM spend,
fleet aggregate. On failure sends a red alert with the run link and the
usual fix list.

Silent no-op when TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are unset, so the
pipeline works before the bot exists.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID; optional RUN_URL, JOB_STATUS.
Usage: python -m btw_engine.notify [--status success|failure] [--run-url U]
"""

import argparse
import os

import httpx


def _rest(path: str, params: dict) -> list[dict]:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = httpx.get(base, headers={"apikey": key,
                                 "Authorization": f"Bearer {key}"},
                  params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def gather() -> str:
    # Only sources the watcher actually runs. Reading the adapter registry
    # instead of the DB keeps retired/manual rows (manual-genesis is a one-off
    # seed with no yaml) out of the health count, where they read as permanent
    # breakage and train the eye to ignore the warning line.
    from btw_engine.watch import load_sources
    live = {c["id"] for c in load_sources()}

    src = [s for s in _rest("source", {
        "select": "id,last_hit_at,last_error,last_error_at,last_yield",
        "adapter": "not.is.null", "order": "id"}) if s["id"] in live]

    # A source that failed on THIS run is broken now, even if last_hit_at
    # still holds yesterday's success. That gap is why 2026-07-21 reported
    # green with three adapters down.
    cutoff = _iso_ago(26)
    failing = [s for s in src
               if (s.get("last_error_at") or "") > cutoff]
    failed_ids = {s["id"] for s in failing}
    ok = [s for s in src if s["last_hit_at"] and s["id"] not in failed_ids]
    silent = [s["id"] for s in src if not s["last_hit_at"]]
    # Green, but the adapter came back empty-handed: the FERC failure mode,
    # where a search keeps answering 200 and simply stops matching anything.
    barren = [s["id"] for s in src
              if s["id"] not in failed_ids and s.get("last_yield") == 0]

    cands = _rest("candidate", {
        "select": "source_id,title,payload,found_at",
        "found_at": "gt." + _iso_ago(26), "order": "found_at.desc",
        "limit": "200"})
    hits = [c for c in cands
            if (c.get("payload") or {}).get("kw_hit") is True]

    staged_u = _rest("unit", {"select": "id", "fact_state": "eq.staging"})
    staged_e = _rest("event", {"select": "id", "fact_state": "eq.staging"})

    cost = _rest("llm_call", {
        "select": "cost_usd", "called_at": "gt." + _iso_ago(24 * 7)})
    spend = sum(float(c["cost_usd"] or 0) for c in cost)

    agg = _rest("aggregate", {
        "select": "value", "metric": "eq.operating_gw",
        "order": "computed_at.desc", "limit": "1"})
    gw = agg[0]["value"] if agg else "?"

    head = (f"🟢 <b>BTW keeper: всё ок</b> — {len(ok)}/{len(src)} "
            f"источников, флот {gw} GW") if not failing else (
        f"🔴 <b>BTW keeper: {len(failing)} адаптер(ов) упало</b> — "
        f"{len(ok)}/{len(src)} зелёных, флот {gw} GW")
    lines = [head,
             f"📥 Кандидатов за сутки: {len(cands)}, kw-хиты: {len(hits)}"]
    for h in hits[:5]:
        lines.append("• " + (h["title"] or "")[:120])
    if staged_u or staged_e:
        lines.append(f"📋 Ждёт ревью: {len(staged_u)} unit-правок, "
                     f"{len(staged_e)} событий — merge review PR = approve")
    for s in failing:
        lines.append(f"🔴 {s['id']}: {(s.get('last_error') or '')[:180]}")
    if barren:
        lines.append("🟡 Отработали вхолостую (0 строк): "
                     + ", ".join(barren))
    if silent:
        lines.append("⚠️ Ни разу не отработали: " + ", ".join(silent))
    lines.append(f"💸 LLM за 7 дней: ${spend:.2f}")
    return "\n".join(lines)


def _iso_ago(hours: int) -> str:
    import datetime as dt
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(hours=hours)).isoformat()


def failure_message(run_url: str) -> str:
    return ("🔴 <b>BTW keeper УПАЛ</b>\n"
            f"Лог: {run_url or 'Actions → daily-intake'}\n"
            "Чинить по порядку:\n"
            "1. Открой лог упавшего шага — обычно ответ источника изменился\n"
            "2. STALE в SLO-шаге = источник молчит дольше SLO; смотри "
            "engine/adapters/*.yaml\n"
            "3. 401/403 к Supabase = проверь секреты SUPABASE_*\n"
            "4. Re-run jobs после фикса; парсеры чинятся PR-ом в "
            "btw_engine/watch.py + фикстура")


def send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram not configured — skipping notify")
        return
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat, "text": text,
                         "parse_mode": "HTML",
                         "disable_web_page_preview": True}, timeout=20)
    r.raise_for_status()
    print("telegram: sent")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default=os.environ.get("JOB_STATUS", "success"))
    ap.add_argument("--run-url", default=os.environ.get("RUN_URL", ""))
    args = ap.parse_args()
    if args.status != "success":
        send(failure_message(args.run_url))
        return
    try:
        send(gather())
    except Exception as e:  # noqa: BLE001 — a broken report becomes an alert
        send(f"🔴 <b>BTW keeper: отчёт не собрался</b>\n{e}\n"
             f"{args.run_url}")


if __name__ == "__main__":
    main()
