#!/bin/sh
set -euo pipefail

LOCATIONS_FILE="${PLUGIN_MASTER_VERSIONS_LOCATIONS_FILE:-}"
CHANGED_DIRS_FILE="${PLUGIN_CHANGED_DIRS_FILE:-}"
FAIL_ON_MISMATCH="${PLUGIN_FAIL_ON_MISMATCH:-false}"

# ── Validate ──────────────────────────────────────────────────────────────────
if [ -z "${LOCATIONS_FILE}" ]; then
    echo ">>> ERROR: PLUGIN_MASTER_VERSIONS_LOCATIONS_FILE is not set"
    exit 1
fi
if [ -z "${CHANGED_DIRS_FILE}" ]; then
    echo ">>> ERROR: PLUGIN_CHANGED_DIRS_FILE is not set"
    exit 1
fi
if [ ! -f "${LOCATIONS_FILE}" ]; then
    echo ">>> ERROR: PLUGIN_MASTER_VERSIONS_LOCATIONS_FILE='${LOCATIONS_FILE}' does not exist"
    exit 1
fi
if [ ! -f "${CHANGED_DIRS_FILE}" ]; then
    echo ">>> ERROR: PLUGIN_CHANGED_DIRS_FILE='${CHANGED_DIRS_FILE}' does not exist"
    exit 1
fi
if [ "${FAIL_ON_MISMATCH}" != "true" ] && [ "${FAIL_ON_MISMATCH}" != "false" ]; then
    echo ">>> ERROR: PLUGIN_FAIL_ON_MISMATCH must be 'true' or 'false' (got '${FAIL_ON_MISMATCH}')"
    exit 1
fi

# ── Sort both files into temp files (skip blank lines) ───────────────────────
tmp_locations=$(mktemp)
tmp_changed=$(mktemp)
trap 'rm -f "${tmp_locations}" "${tmp_changed}"' EXIT

grep -v '^[[:space:]]*$' "${LOCATIONS_FILE}"   | sort > "${tmp_locations}" || true
grep -v '^[[:space:]]*$' "${CHANGED_DIRS_FILE}" | sort > "${tmp_changed}"  || true

echo ">>> Locations from PR  ($(wc -l < "${tmp_locations}") entries):"
cat "${tmp_locations}" | while IFS= read -r l; do echo "    ${l}"; done

echo ">>> Changed dirs       ($(wc -l < "${tmp_changed}") entries):"
cat "${tmp_changed}" | while IFS= read -r l; do echo "    ${l}"; done
echo ""

# ── Compare ───────────────────────────────────────────────────────────────────
# comm -23: in changed but NOT in locations  → changed but not released
# comm -13: in locations but NOT in changed  → mentioned in PR but nothing changed
missing_from_pr=$(comm -23 "${tmp_changed}" "${tmp_locations}")
extra_in_pr=$(comm -13 "${tmp_changed}" "${tmp_locations}")

if [ -z "${missing_from_pr}" ] && [ -z "${extra_in_pr}" ]; then
    echo ">>> OK: every changed directory is covered in the PR description."
    exit 0
fi

if [ -n "${missing_from_pr}" ]; then
    printf '\033[33m>>> WARNING: MISSING from PR description (changed but not described in description):\033[0m\n'
    printf '%s\n' "${missing_from_pr}" | while IFS= read -r d; do
        printf '\033[33m    %s\033[0m\n' "${d}"
    done
    echo ""
fi

if [ -n "${extra_in_pr}" ]; then
    printf '\033[33m>>> WARNING: Extra in PR description (mentioned but no files changed):\033[0m\n'
    printf '%s\n' "${extra_in_pr}" | while IFS= read -r d; do
        printf '\033[33m    %s\033[0m\n' "${d}"
    done
    echo ""
fi

if [ "${FAIL_ON_MISMATCH}" = "true" ]; then
    printf '\033[31m>>> ERROR: mismatch found — failing pipeline (PLUGIN_FAIL_ON_MISMATCH=true)\033[0m\n'
    exit 1
else
    printf '\033[33m>>> WARNING: mismatch found — pipeline continues (PLUGIN_FAIL_ON_MISMATCH=false)\033[0m\n'
fi
