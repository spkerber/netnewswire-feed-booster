#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-export}"
if [[ -n "${RSS_PRIVATE_ENV:-}" ]]; then
  PRIVATE_ENV_FILE="${RSS_PRIVATE_ENV}"
elif [[ -f "data/private.env" ]]; then
  PRIVATE_ENV_FILE="data/private.env"
else
  PRIVATE_ENV_FILE="${BANDCAMP_TOKEN_FILE:-data/private-modal.env}"
fi
if [[ -f "${PRIVATE_ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "${PRIVATE_ENV_FILE}"
  set +a
fi
RSS_PROFILE="${RSS_PROFILE:-me}"
if [[ -f "data/sources.${RSS_PROFILE}.json" ]]; then
  DEFAULT_RSS_SOURCES_FILE="data/sources.${RSS_PROFILE}.json"
else
  DEFAULT_RSS_SOURCES_FILE="data/sources.json"
fi
if [[ -f "data/subscription-history.${RSS_PROFILE}.json" ]]; then
  DEFAULT_RSS_HISTORY_FILE="data/subscription-history.${RSS_PROFILE}.json"
else
  DEFAULT_RSS_HISTORY_FILE="data/subscription-history.json"
fi
RSS_SOURCES_FILE="${RSS_SOURCES_FILE:-${DEFAULT_RSS_SOURCES_FILE}}"
RSS_HISTORY_FILE="${RSS_HISTORY_FILE:-${DEFAULT_RSS_HISTORY_FILE}}"
MODAL_APP_NAME="${MODAL_APP_NAME:-rss-feed-bridge}"
MODAL_SECRET_NAME="${MODAL_SECRET_NAME:-rss-feed-bridge-token}"
MODAL_VOLUME_NAME="${MODAL_VOLUME_NAME:-${MODAL_APP_NAME}-cache}"
RSS_FEED_BASE="${RSS_FEED_BASE:-${MODAL_FEED_BASE:-}}"
RSS_FEED_TOKEN="${RSS_FEED_TOKEN:-${BANDCAMP_FEED_TOKEN:-}}"
OUT_FILE="${NETNEWSWIRE_OPML_OUT:-exports/${RSS_PROFILE}-netnewswire-hosted.opml}"
MODAL_BIN="${MODAL_BIN:-.venv-modal/bin/modal}"
NETNEWSWIRE_OPML="${NETNEWSWIRE_OPML:-${HOME}/Library/Containers/com.ranchero.NetNewsWire-Evergreen/Data/Library/Application Support/NetNewsWire/Accounts/2_iCloud/Subscriptions.opml}"

load_token() {
  if [[ ! -f "${PRIVATE_ENV_FILE}" ]]; then
    echo "Missing ${PRIVATE_ENV_FILE}. Expected RSS_FEED_TOKEN=... for hosted generated feed URLs." >&2
    exit 1
  fi
  if [[ -z "${RSS_FEED_TOKEN:-}" ]]; then
    echo "${PRIVATE_ENV_FILE} does not define RSS_FEED_TOKEN." >&2
    exit 1
  fi
  if [[ -z "${RSS_FEED_BASE:-}" ]]; then
    echo "${PRIVATE_ENV_FILE} or the environment must define RSS_FEED_BASE." >&2
    exit 1
  fi
}

deploy_modal() {
  load_token
  if [[ ! -x "${MODAL_BIN}" ]]; then
    echo "Missing ${MODAL_BIN}. Create .venv-modal and install .[modal] before deploying." >&2
    exit 1
  fi
  export RSS_PROFILE RSS_SOURCES_FILE RSS_FEED_TOKEN MODAL_APP_NAME MODAL_SECRET_NAME MODAL_VOLUME_NAME FULL_FAN_SOURCE_IDS
  "${MODAL_BIN}" secret create "${MODAL_SECRET_NAME}" --from-dotenv "${PRIVATE_ENV_FILE}" --force
  "${MODAL_BIN}" deploy modal_bandcamp_app.py
}

export_opml() {
  load_token
  PYTHONPATH=src python3 -m unittest discover -s tests
  PYTHONPATH=src python3 -m netnewswire_feed_booster \
    --data "${RSS_SOURCES_FILE}" \
    --history "${RSS_HISTORY_FILE}" \
    export-opml \
    --profile "${RSS_PROFILE}" \
    --out "${OUT_FILE}" \
    --bandcamp-feed-base "${RSS_FEED_BASE}" \
    --bandcamp-feed-token "${RSS_FEED_TOKEN}"
  python3 -m xml.etree.ElementTree "${OUT_FILE}"
  echo "Wrote ${OUT_FILE}"
}

verify_netnewswire() {
  export_opml
  if [[ ! -f "${NETNEWSWIRE_OPML}" ]]; then
    echo "Missing NetNewsWire OPML: ${NETNEWSWIRE_OPML}" >&2
    exit 1
  fi
  PYTHONPATH=src python3 -m netnewswire_feed_booster \
    --data "${RSS_SOURCES_FILE}" \
    --history "${RSS_HISTORY_FILE}" \
    verify-netnewswire \
    "${NETNEWSWIRE_OPML}" \
    --expected "${OUT_FILE}" \
    --profile "${RSS_PROFILE}"
}

repair_netnewswire() {
  export_opml
  if [[ ! -f "${NETNEWSWIRE_OPML}" ]]; then
    echo "Missing NetNewsWire OPML: ${NETNEWSWIRE_OPML}" >&2
    exit 1
  fi

  set +e
  PYTHONPATH=src python3 -m netnewswire_feed_booster \
    --data "${RSS_SOURCES_FILE}" \
    --history "${RSS_HISTORY_FILE}" \
    verify-netnewswire \
    "${NETNEWSWIRE_OPML}" \
    --expected "${OUT_FILE}" \
    --profile "${RSS_PROFILE}"
  verify_status=$?
  set -e
  if [[ "${verify_status}" -eq 0 ]]; then
    echo "No NetNewsWire repair needed."
    return 0
  fi
  if [[ "${verify_status}" -ne 1 ]]; then
    echo "NetNewsWire verification failed unexpectedly; not repairing." >&2
    exit "${verify_status}"
  fi
  if pgrep -x NetNewsWire >/dev/null; then
    echo "NetNewsWire is running. Quit NetNewsWire before repairing subscriptions." >&2
    exit 1
  fi

  backup_path="${NETNEWSWIRE_OPML}.backup-$(date +%Y%m%d-%H%M%S)"
  cp "${NETNEWSWIRE_OPML}" "${backup_path}"
  cp "${OUT_FILE}" "${NETNEWSWIRE_OPML}"
  python3 -m xml.etree.ElementTree "${NETNEWSWIRE_OPML}"
  PYTHONPATH=src python3 -m netnewswire_feed_booster \
    --data "${RSS_SOURCES_FILE}" \
    --history "${RSS_HISTORY_FILE}" \
    verify-netnewswire \
    "${NETNEWSWIRE_OPML}" \
    --expected "${OUT_FILE}" \
    --profile "${RSS_PROFILE}"
  echo "Repaired NetNewsWire subscriptions. Backup: ${backup_path}"
}

case "${MODE}" in
  deploy-modal)
    deploy_modal
    ;;
  export)
    export_opml
    ;;
  verify-netnewswire)
    verify_netnewswire
    ;;
  repair-netnewswire)
    repair_netnewswire
    ;;
  all)
    deploy_modal
    export_opml
    ;;
  *)
    echo "Usage: $0 [deploy-modal|export|verify-netnewswire|repair-netnewswire|all]" >&2
    exit 2
    ;;
esac
