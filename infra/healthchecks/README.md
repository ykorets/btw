# Watchtower — self-hosted Healthchecks (via Coolify)

Dead-man switch for every scheduled job we run. A job that finishes
successfully GETs its ping URL; if a check goes silent past its grace
window, Healthchecks alerts Telegram. Silence stops looking like success.

First tenant: `btw` daily-intake (`HEARTBEAT_URL`). Add one check per
service after that (KPI crons, GTM pipelines, backups).

## Deploy in Coolify (~10 min)

1. **New Resource → Docker Compose**, paste `docker-compose.yml` (or point
   Coolify at the repo/folder).
2. **Domain**: on the service set `https://hc.<domain>:8000` — Coolify's
   proxy handles TLS/routing to the container port. DNS A-record for
   `hc.<domain>` must point at the Coolify server.
3. **Environment variables** (values — see `.env.example`):
   `SITE_ROOT`, `ALLOWED_HOSTS`, `SECRET_KEY`
   (`python3 -c "import secrets; print(secrets.token_urlsafe(50))"`),
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_NAME`. Registration is closed by
   design (`REGISTRATION_OPEN=False` is baked into the compose).
4. **Deploy.**
5. Create your account — Coolify → service → Terminal:
   `/opt/healthchecks/manage.py createsuperuser`
6. Log in at `https://hc.<domain>`, create project **Watchtower**.
7. **Telegram webhook** (once; from any machine, `$TOKEN` = bot token):
   `curl "https://api.telegram.org/bot$TOKEN/setWebhook?url=https://hc.<domain>/integrations/telegram/bot/"`
   This claims the bot's webhook for this instance. If the same bot already
   serves another webhook-based app, create a dedicated bot for Watchtower
   (@BotFather → /newbot) — long-polling apps are unaffected either way.
8. Integrations → Telegram → connect (the bot asks which chat).

## First check: btw-daily

- Add Check → name `btw-daily`, Period **1 day**, Grace **6 hours**
  (GitHub cron fires with slack; 6h avoids false alarms).
- Copy the ping URL (`https://hc.<domain>/ping/<uuid>`).
- GitHub → `ykorets/btw` → Settings → Secrets → Actions →
  `HEARTBEAT_URL` = that URL.
- Engine side is already wired: `watch.py` pings it at the end of every
  successful daily run; `daily.yml` passes the secret.
- Verify: dispatch daily-intake once → the check flips to "up".

## Who watches the watchman

This instance dies silently with its server. Close the loop with ONE free
external check pointed at `https://hc.<domain>/` (UptimeRobot free tier is
enough). It watches only the monitor; everything else is watched from here.

## Notes

- Data = SQLite on the `hc-data` volume; include it in server backups.
- `DB=sqlite` + `DB_NAME=/data/hc.sqlite` are hardcoded in the compose: without
  them the app tries to open the DB in its working dir and dies with
  "unable to open database file".
- `ALLOWED_HOSTS` must include `localhost` (see `.env.example`) or the
  container never turns healthy and Traefik won't route the domain.
- Upgrade: redeploy in Coolify (image pull); migrations run on start.
- This folder is generic infra parked in the btw repo — it belongs in its
  own infra repo once a second service attaches.
