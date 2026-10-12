# Remotor

Telegram-бот для добавления торрентов (magnet или `.torrent`) в **transmission-daemon** . Доступ только у пользователей из списка `ALLOWED_USER_IDS`.

## Возможности

- Magnet-ссылки и файлы `.torrent`
- Выбор папки загрузки (`/folder`)
- Статус и отмена загрузок (`/status`, `/cancel`)
- Настройки сессии Transmission (`/settings`)
- Опциональный HTTP(S)-прокси для announce/scrape трекеров (`TRANSMISSION_PROXY_URL`)

## Требования

- Python 3.11+
- [transmission-daemon](https://transmissionbt.com/) с RPC на `127.0.0.1`
- Токен бота от [@BotFather](https://t.me/BotFather)

```bash
sudo apt update
sudo apt install -y transmission-daemon python3-venv
```

Подробная настройка демона, прав на каталог и прокси — в [deploy/transmission-notes.md](deploy/transmission-notes.md).

## Быстрый старт (разработка)

```bash
git clone <URL-репозитория> remotor
cd remotor

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# отредактируйте .env: BOT_TOKEN, ALLOWED_USER_IDS, пути и RPC Transmission

./remotor.sh start
```

Лог при запуске без systemd: `remotor.log`. Остановка: `./remotor.sh stop`.

Проверка:

```bash
pytest
```



## Переменные окружения


| Переменная                 | Описание                                                            |
| -------------------------- | ------------------------------------------------------------------- |
| `BOT_TOKEN`                | Токен Telegram-бота                                                 |
| `ALLOWED_USER_IDS`         | ID пользователей Telegram через запятую                             |
| `TRANSMISSION_*`           | Хост, порт, логин и пароль RPC                                      |
| `TRANSMISSION_SYSTEM_USER` | UID/GID для проверки прав на каталог (обычно `debian-transmission`) |
| `TRANSMISSION_PROXY_URL`   | SOCKS/HTTP прокси для трекеров (необязательно)                      |
| `DEFAULT_DOWNLOAD_DIR`     | Папка загрузки по умолчанию                                         |
| `ALLOWED_ROOTS`            | Корни для обзора папок в `/folder`                                  |
| `DB_PATH`                  | SQLite-база состояния (по умолчанию `remotor.db`)                   |


Полный шаблон — в [.env.example](.env.example). **Не коммитьте** `.env` — он уже в `.gitignore`.

## Деплой



### Вариант A: user systemd (один пользователь на сервере)

После настройки `.env` и venv:

```bash
./remotor.sh install-service
```

Скрипт подставит путь к проекту в [deploy/remotor-bot.user.service](deploy/remotor-bot.user.service), включит автозапуск и при необходимости `loginctl enable-linger`.

Управление:

```bash
./remotor.sh {start|stop|restart|status}
journalctl --user -u remotor-bot.service -f
```



### Вариант B: системный сервис

Скопируйте проект в `/opt/remotor`, создайте пользователя `remotor`, установите зависимости от его имени и используйте [deploy/remotor-bot.service](deploy/remotor-bot.service). Пошагово — раздел «Бот» в [deploy/transmission-notes.md](deploy/transmission-notes.md).

### Запуск вручную

```bash
source .venv/bin/activate
python -m remotor
```



## Безопасность

- RPC Transmission должен слушать только `127.0.0.1`, с whitelist и паролем.
- Бот не отвечает пользователям вне `ALLOWED_USER_IDS`.
- Обзор файловой системы в `/folder` ограничен `ALLOWED_ROOTS`.
- Секреты и локальные логи не должны попадать в git (см. `.gitignore`).

