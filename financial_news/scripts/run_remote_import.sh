#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_SHARE_ROOT="/Volumes/adobi/d-ai-trader"
DEFAULT_OUTPUT_ROOT="${DEFAULT_SHARE_ROOT}/Knowledge_Bases/financial_news"
OUTPUT_ROOT="${FINANCIAL_NEWS_OUTPUT_ROOT:-${DEFAULT_OUTPUT_ROOT}}"
STATE_PATH="${FINANCIAL_NEWS_STATE_PATH:-${OUTPUT_ROOT}/.state/ingest_state.json}"
PATH_REMAP_TO="${FINANCIAL_NEWS_PATH_REMAP_TO:-${DEFAULT_SHARE_ROOT}}"
VENV_PYTHON="${PACKAGE_DIR}/.venv/bin/python"

if [[ -x "${VENV_PYTHON}" ]]; then
  PYTHON_BIN="${VENV_PYTHON}"
else
  PYTHON_BIN="$(command -v python3)"
fi

case "${OUTPUT_ROOT}" in
  "${PACKAGE_DIR}"|"${PACKAGE_DIR}"/*)
    echo "Refusing to write imported output inside the repo checkout: ${OUTPUT_ROOT}" >&2
    echo "Set FINANCIAL_NEWS_OUTPUT_ROOT to an external vault path (recommended: ${DEFAULT_OUTPUT_ROOT})." >&2
    exit 2
    ;;
esac

if [[ "${OUTPUT_ROOT}" == /Volumes/adobi/* && ! -d /Volumes/adobi ]]; then
  echo "Expected mounted share root /Volumes/adobi is not available." >&2
  echo "Mount the share first or override FINANCIAL_NEWS_OUTPUT_ROOT." >&2
  exit 2
fi

ARGS=(
  -m financial_news
  --output-root "${OUTPUT_ROOT}"
  --state-path "${STATE_PATH}"
)

if [[ -n "${FINANCIAL_NEWS_DSN:-}" ]]; then
  ARGS+=(--dsn "${FINANCIAL_NEWS_DSN}")
fi

if [[ -n "${FINANCIAL_NEWS_PATH_REMAP_FROM:-}" ]]; then
  ARGS+=(--path-remap "${FINANCIAL_NEWS_PATH_REMAP_FROM}=${PATH_REMAP_TO}")
fi

ARGS+=("$@")

printf 'financial_news remote import\n'
printf '  package_dir: %s\n' "${PACKAGE_DIR}"
printf '  output_root: %s\n' "${OUTPUT_ROOT}"
printf '  state_path : %s\n' "${STATE_PATH}"
printf '  dsn source : %s\n' "${FINANCIAL_NEWS_DSN:+FINANCIAL_NEWS_DSN}${FINANCIAL_NEWS_DSN:-local fallback candidates}"
printf '  remap      : %s\n' "${FINANCIAL_NEWS_PATH_REMAP_FROM:+${FINANCIAL_NEWS_PATH_REMAP_FROM} => ${PATH_REMAP_TO}}${FINANCIAL_NEWS_PATH_REMAP_FROM:-disabled}"

cd "${PACKAGE_DIR}"
export PYTHONPATH="${PACKAGE_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON_BIN}" "${ARGS[@]}"
