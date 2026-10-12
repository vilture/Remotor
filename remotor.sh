#!/usr/bin/env bash
# Управление Telegram-ботом Remotor (start | stop | restart | status | install-service).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="${ROOT}/.remotor.pid"
LOG="${ROOT}/remotor.log"
PYTHON="${ROOT}/.venv/bin/python"
UNIT_NAME="remotor-bot.service"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
USER_UNIT="${USER_UNIT_DIR}/${UNIT_NAME}"
UNIT_TEMPLATE="${ROOT}/deploy/remotor-bot.user.service"

usage() {
    echo "Использование: $0 {start|stop|restart|status|install-service}"
    echo "  Каталог: ${ROOT}"
    echo "  Лог:     ${LOG}"
    echo ""
    echo "  install-service — user systemd: автозапуск после перезагрузки и перезапуск при сбоях."
    echo "  stop — штатная остановка, systemd не поднимет бота снова до start/enable/boot."
}

systemd_available() {
    [[ -f "${USER_UNIT}" ]] && systemctl --user show-environment &>/dev/null
}

pid_in_root() {
    local pid="$1"
    local comm cwd
    [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null || return 1
    comm="$(ps -p "${pid}" -o comm= 2>/dev/null || true)"
    [[ "${comm}" == *python* ]] || return 1
    cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || echo "")"
    [[ "${cwd}" == "${ROOT}" ]]
}

find_pid() {
    local pid=""
    if [[ -f "${PIDFILE}" ]]; then
        pid="$(cat "${PIDFILE}" 2>/dev/null || true)"
        if pid_in_root "${pid}"; then
            echo "${pid}"
            return 0
        fi
    fi
    local candidate
    while IFS= read -r candidate; do
        if pid_in_root "${candidate}"; then
            echo "${candidate}"
            return 0
        fi
    done < <(pgrep -f "[p]ython -m remotor" 2>/dev/null || true)
}

require_venv() {
    if [[ ! -x "${PYTHON}" ]]; then
        echo "Ошибка: нет ${PYTHON}. Создайте venv: python3 -m venv .venv && .venv/bin/pip install -e ."
        exit 1
    fi
}

require_env() {
    if [[ ! -f "${ROOT}/.env" ]]; then
        echo "Ошибка: нет ${ROOT}/.env (скопируйте из .env.example)."
        exit 1
    fi
}

stop_legacy() {
    local pid
    pid="$(find_pid)"
    if [[ -z "${pid}" ]]; then
        rm -f "${PIDFILE}"
        return 0
    fi
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 15); do
        if ! kill -0 "${pid}" 2>/dev/null; then
            rm -f "${PIDFILE}"
            echo "Остановлен устаревший процесс (PID ${pid})."
            return 0
        fi
        sleep 1
    done
    kill -9 "${pid}" 2>/dev/null || true
    rm -f "${PIDFILE}"
    echo "Остановлен устаревший процесс (PID ${pid})."
}

install_service() {
    require_venv
    require_env
    if [[ ! -f "${UNIT_TEMPLATE}" ]]; then
        echo "Ошибка: нет ${UNIT_TEMPLATE}"
        exit 1
    fi
    mkdir -p "${USER_UNIT_DIR}"
    sed "s|@ROOT@|${ROOT}|g" "${UNIT_TEMPLATE}" > "${USER_UNIT}"
    systemctl --user daemon-reload
    if loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q 'Linger=no'; then
        echo "Включаю linger (автозапуск user-сервисов без активной SSH-сессии)..."
        loginctl enable-linger "$(id -un)"
    fi
    stop_legacy
    systemctl --user enable "${UNIT_NAME}"
    systemctl --user start "${UNIT_NAME}"
    echo "Установлен ${USER_UNIT}"
    echo "Включён автозапуск: systemctl --user is-enabled ${UNIT_NAME}"
    systemctl --user --no-pager status "${UNIT_NAME}" || true
}

start_bot() {
    require_venv
    require_env
    if systemd_available; then
        systemctl --user start "${UNIT_NAME}"
        echo "Запущен через systemd (user). Лог: journalctl --user -u ${UNIT_NAME} -f"
        return 0
    fi
    local pid
    pid="$(find_pid)"
    if [[ -n "${pid}" ]]; then
        echo "Бот уже запущен (PID ${pid})."
        exit 0
    fi
    cd "${ROOT}"
    nohup "${PYTHON}" -m remotor >> "${LOG}" 2>&1 &
    pid=$!
    echo "${pid}" > "${PIDFILE}"
    sleep 1
    if kill -0 "${pid}" 2>/dev/null; then
        echo "Запущен PID ${pid}, лог: ${LOG}"
    else
        echo "Ошибка: процесс сразу завершился. Последние строки лога:"
        tail -n 20 "${LOG}" 2>/dev/null || true
        rm -f "${PIDFILE}"
        exit 1
    fi
}

stop_bot() {
    if systemd_available; then
        if systemctl --user is-active --quiet "${UNIT_NAME}"; then
            systemctl --user stop "${UNIT_NAME}"
            echo "Остановлен (systemd user, без автоперезапуска)."
        else
            echo "Сервис ${UNIT_NAME} не активен."
        fi
        stop_legacy
        return 0
    fi
    local pid
    pid="$(find_pid)"
    if [[ -z "${pid}" ]]; then
        echo "Бот не запущен."
        rm -f "${PIDFILE}"
        exit 0
    fi
    kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 15); do
        if ! kill -0 "${pid}" 2>/dev/null; then
            rm -f "${PIDFILE}"
            echo "Остановлен (PID ${pid})."
            return 0
        fi
        sleep 1
    done
    echo "Принудительное завершение PID ${pid}..."
    kill -9 "${pid}" 2>/dev/null || true
    rm -f "${PIDFILE}"
    echo "Остановлен."
}

status_bot() {
    if systemd_available; then
        systemctl --user --no-pager status "${UNIT_NAME}" || true
        return 0
    fi
    local pid
    pid="$(find_pid)"
    if [[ -z "${pid}" ]]; then
        echo "Бот не запущен."
        rm -f "${PIDFILE}"
        exit 0
    fi
    echo "Бот запущен, PID ${pid}"
    if [[ -f "${LOG}" ]]; then
        echo "--- последние строки ${LOG} ---"
        tail -n 5 "${LOG}" || true
    fi
}

main() {
    if [[ $# -ne 1 ]]; then
        usage
        exit 1
    fi
    case "$1" in
        start) start_bot ;;
        stop) stop_bot ;;
        restart)
            if systemd_available; then
                systemctl --user restart "${UNIT_NAME}"
                echo "Перезапущен (systemd user)."
            else
                stop_bot
                start_bot
            fi
            ;;
        status) status_bot ;;
        install-service) install_service ;;
        *)
            usage
            exit 1
            ;;
    esac
}

main "$@"
