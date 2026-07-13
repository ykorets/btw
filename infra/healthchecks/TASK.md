# TASK: развернуть Watchtower (self-hosted Healthchecks) через Coolify

Контекст-выгрузка из сессии 2026-07-12. Задача самодостаточная — всё
нужное в этой папке (`infra/healthchecks/` в репо btw).

## Зачем

Dead-man switch для всех крон-сервисов. Первый клиент — BTW daily-intake:
GitHub cron умирает молча (отключение после 60 дней неактивности, сломанный
YAML, лимиты Actions), и тишина неотличима от успеха. Telegram-бот BTW уже
шлёт отчёты/алерты ИЗ воркфлоу (notify.py, M8.1), но не может заметить, что
воркфлоу вообще не запустился. Дозорный обязан жить снаружи GitHub.
Решение: Healthchecks (open source) на своём сервере, деплой через Coolify.
Дальше к нему прикрепляются все сервисы (KPI-кроны, GTM, бэкапы) — по чеку
на сервис.

## Статус: РАЗВЁРНУТО (2026-07-12/13)

Живёт на hc.kpicreatives.com. Чек `btw-daily` зелёный, Telegram-алерты
проверены циклом DOWN→UP (M4 DoD закрыт). Секрет `HEARTBEAT_URL` заведён.
Ниже — исходная инструкция (пригодится для следующих инстансов).

## Что в бандле

- `docker-compose.yml` — готов под Coolify (New Resource → Docker Compose;
  TLS/домен на Coolify-прокси, порт 8000 через expose, том hc-data/SQLite,
  REGISTRATION_OPEN=False зашит).
- `.env.example` — список переменных для вкладки Environment Variables.
- `README.md` — пошаговый деплой (8 шагов) + создание первого чека.
- Сторона движка btw полностью готова: `watch.py::heartbeat()` делает GET
  на `HEARTBEAT_URL` в конце удачного прогона; `daily.yml` уже передаёт
  секрет.

## Шаги деплоя (детали в README.md)

1. Coolify: New Resource → Docker Compose → этот compose.
2. Домен `https://hc.<домен>:8000` на сервисе + DNS A-запись.
3. Env vars из `.env.example` (SECRET_KEY сгенерировать).
4. Deploy → в терминале сервиса `/opt/healthchecks/manage.py createsuperuser`.
5. Telegram: setWebhook (README шаг 7). ВАЖНО: вебхук бота перейдёт на этот
   инстанс — если основной бот работает через webhook где-то ещё, завести
   отдельного бота через @BotFather.
6. Проект Watchtower → чек `btw-daily`: Period 1 day, Grace 6 hours.
7. Ping-URL чека → GitHub `ykorets/btw` → Settings → Secrets → Actions →
   секрет `HEARTBEAT_URL`.
8. Верификация: Actions → daily-intake → Run workflow → чек становится «up».

## Открытые хвосты

- Внешний дозор для самого Watchtower: один бесплатный чек (UptimeRobot)
  на `https://hc.<домен>/`.
- Эту папку после обкатки перенести в отдельный infra-репо (здесь она
  припаркована для удобства).
