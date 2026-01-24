#!/usr/bin/env bash
set -euo pipefail

SUCCESS=10
PAUSE=3600
QUERY="Python разработчик"
PAGES=2
MAX=50
DELAY=3
MESSAGE=""
CYCLES=0
FORCE=""
VISIBLE=0
SERVER=0
USER_ID=""

usage() {
  cat <<EOF
Usage: ./start-project.sh [options]

Options:
  --success N        Кол-во успешных откликов за цикл (default: ${SUCCESS})
  --pause SEC        Пауза между циклами в секундах (default: ${PAUSE})
  --query TEXT       Поисковый запрос (default: ${QUERY})
  --pages N          Сколько страниц смотреть (default: ${PAGES})
  --max N            Макс. попыток на цикл (default: ${MAX})
  --delay SEC        Задержка между попытками (default: ${DELAY})
  --message TEXT     Сопроводительное письмо
  --cycles N         Кол-во циклов (0 = бесконечно)
  --force            Форсировать отклик (обходит processed)
  --visible          Включить видимый браузер (BROWSER_HEADLESS=false)
  --server           Запустить API сервер + Admin UI (вместо цикла откликов)
  --user-id SLUG     Запуск откликов от имени пользователя (использует data/users/<slug>/...)
  -h, --help         Показать помощь

Examples:
  ./start-project.sh
  ./start-project.sh --success 10 --pause 3600
  ./start-project.sh --query "Python разработчик" --pages 2 --max 50 --success 10
  ./start-project.sh --server
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --success)
      SUCCESS="$2"; shift 2;;
    --pause)
      PAUSE="$2"; shift 2;;
    --query)
      QUERY="$2"; shift 2;;
    --pages)
      PAGES="$2"; shift 2;;
    --max)
      MAX="$2"; shift 2;;
    --delay)
      DELAY="$2"; shift 2;;
    --message)
      MESSAGE="$2"; shift 2;;
    --cycles)
      CYCLES="$2"; shift 2;;
    --force)
      FORCE="--force"; shift 1;;
    --visible)
      VISIBLE=1; shift 1;;
    --server)
      SERVER=1; shift 1;;
    --user-id)
      USER_ID="$2"; shift 2;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

# If the venv exists, prefer it.
PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

# Production-friendly defaults
export PYTHONUNBUFFERED=1
if [[ "${VISIBLE}" -eq 1 ]]; then
  export BROWSER_HEADLESS=false
else
  export BROWSER_HEADLESS=true
fi

mkdir -p logs
RUN_LOG="logs/run_cycle_$(date +%Y-%m-%d_%H-%M-%S).log"

if [[ "${SERVER}" -eq 1 ]]; then
  SERVER_LOG="logs/server_$(date +%Y-%m-%d_%H-%M-%S).log"
  echo "[server] starting... log=${SERVER_LOG}"
  "${PYTHON_BIN}" -u run_project.py 2>&1 | tee -a "${SERVER_LOG}"
  exit ${PIPESTATUS[0]}
fi

# If message is empty, pass nothing.
MSG_ARGS=()
if [[ -n "${MESSAGE}" ]]; then
  MSG_ARGS=("--message" "${MESSAGE}")
fi

exec "${PYTHON_BIN}" run_cycle_automation.py \
  --user-id "${USER_ID}" \
  --query "${QUERY}" \
  --pages "${PAGES}" \
  --max "${MAX}" \
  --batch-success "${SUCCESS}" \
  --pause "${PAUSE}" \
  --delay "${DELAY}" \
  ${FORCE} \
  --cycles "${CYCLES}" \
  ${MSG_ARGS[@]+"${MSG_ARGS[@]}"} \
  2>&1 | tee -a "${RUN_LOG}"
