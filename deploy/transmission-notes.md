# Transmission на Ubuntu (deb)

Необходимы пакеты `transmission-daemon` и `python3-venv`. В репозитории нет бинарников: бот на чистом Python и говорит с демоном по RPC `127.0.0.1`. Скрипт `download_torrent.sh` бот не вызывает.

Демон работает от пользователя `debian-transmission` и сам пишет файлы. Бот только управляет им.

## Демон

```bash
sudo apt update
sudo apt install -y transmission-daemon python3-venv
sudo systemctl stop transmission-daemon
```

В `/etc/transmission-daemon/settings.json` (править при остановленном демоне, иначе он перезапишет файл):

- `rpc-bind-address`: `127.0.0.1`
- `rpc-whitelist-enabled`: `true`
- `rpc-whitelist`: `127.0.0.1`
- `rpc-authentication-required`: `true`
- `rpc-username` и `rpc-password` — те же значения, что `TRANSMISSION_USER` и `TRANSMISSION_PASSWORD` в `.env`

Папку каждого торрента задаёт бот. После правки: `sudo systemctl start transmission-daemon`.

Права на каталог загрузки:

Замените `DOWNLOAD_DIR` на значение `DEFAULT_DOWNLOAD_DIR` из `.env`:

```bash
DOWNLOAD_DIR=/var/lib/transmission-daemon/downloads
sudo mkdir -p "$DOWNLOAD_DIR"
sudo chgrp debian-transmission "$DOWNLOAD_DIR"
sudo chmod 2775 "$DOWNLOAD_DIR"
```

Пользователь бота должен иметь право проходить в этот каталог. Пишет демон.

## Бот

```bash
sudo useradd --system --create-home --home-dir /opt/remotor remotor
sudo mkdir -p /opt/remotor
# скопировать проект в /opt/remotor
sudo cp -a . /opt/remotor
sudo chown -R remotor:remotor /opt/remotor
sudo -u remotor python3 -m venv /opt/remotor/.venv
sudo -u remotor /opt/remotor/.venv/bin/pip install /opt/remotor
sudo cp deploy/remotor-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now remotor-bot
```

`.env` лежит в `/opt/remotor/.env`. `ALLOWED_USER_IDS` — Telegram user id через запятую.

## Тестовый ПК

Путь загрузки произвольный. В `.env` укажите существующую папку в `DEFAULT_DOWNLOAD_DIR`, родительский каталог в `ALLOWED_ROOTS` и пользователя демона в `TRANSMISSION_SYSTEM_USER`.

## DHT/PEX и скрипт `download_torrent.sh`

`transmission-cli -m -D` включает **DHT** и **PEX**. У демона то же самое: `dht-enabled`, `pex-enabled`, `lpd-enabled` (бот при старте выставляет DHT/PEX/LPD через RPC).

Это помогает найти **часть** пиров без трекера, но **не заменяет announce** на HTTP-трекер (t-ru и др.). Если провайдер режет трекер, в логе Transmission будет `Tracker did not respond`, а с сайта трекера сиды всё равно видны.

## Обход блокировки HTTP-трекера (прокси)

Transmission 4.0.5+ понимает ключ сессии **`proxy-url`**: через него идут только **HTTP(S)** запросы (announce/scrape, web-seed), **не** BitTorrent-пиры и **не** UDP/DHT.

В `.env` бота Remotor:

```env
TRANSMISSION_PROXY_URL=socks5h://USER:PASS@HOST:1080
```

Схема **`socks5h`** — DNS резолвит прокси (важно при блокировках). После смены прокси перезапустите бота (он применит настройку к демону) и принудительно reannounce:

```bash
transmission-remote 127.0.0.1:9091 -t all -n
```

Проверка announce:

```bash
transmission-remote 127.0.0.1:9091 -t ID -i | grep -i announce
```

Нужно `lastAnnounceSucceeded` и `lastAnnouncePeerCount` > 0.

Альтернатива без `.env`: в `settings.json` демона (демон **остановлен**) добавить `"proxy-url": "socks5h://…"` или поднять демон с переменными `ALL_PROXY` / `HTTPS_PROXY` (если `proxy-url` не задан).

Прокси должен быть **ваш** (VPS, домашний сервер за границей, платный SOCKS). 
