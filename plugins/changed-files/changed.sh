#!/bin/bash

set -e
set -u
set -o pipefail

OUTPUT_FILE="${PLUGIN_OUTPUT_FILE:-changed.txt}"
OUTPUT_TYPE="${PLUGIN_OUTPUT_TYPE:-files}"
FOLDER_DEPTH="${PLUGIN_FOLDER_DEPTH:-0}"
DEDUP="${PLUGIN_DEDUP:-true}"
BASE_PATH="${PLUGIN_BASE_PATH:-}"

OUTPUT_DIR=$(dirname "$OUTPUT_FILE")
if [ ! -d "$OUTPUT_DIR" ]; then
    echo "Error: Output directory '$OUTPUT_DIR' does not exist." >&2
    exit 1
fi

changed_files=$(echo "${CI_PIPELINE_FILES:-}" | tr -d '["]' | tr ',' '\n')

if [ $? -ne 0 ]; then
    echo "Error: Failed to get changed files using git diff." >&2
    echo "$changed_files" >&2
    exit 1
fi

if [ -n "${BASE_PATH}" ] && [ "${BASE_PATH}" != "." ]; then
    BASE_PATH="${BASE_PATH%/}"
    changed_files=$(printf '%s' "$changed_files" | grep "^${BASE_PATH}/" | sed "s|^${BASE_PATH}/||" || true)
fi

if [ -n "${PLUGIN_EXCLUDE_FILES_REGEX:-}" ]; then
    changed_files=$(printf '%s' "$changed_files" | grep -Ev "${PLUGIN_EXCLUDE_FILES_REGEX}" || true)
fi

if [ -n "${PLUGIN_INCLUDE_FILES_REGEX:-}" ]; then
    changed_files=$(printf '%s' "$changed_files" | grep -E "${PLUGIN_INCLUDE_FILES_REGEX}" || true)
fi

case $OUTPUT_TYPE in
    files)
        output="$changed_files"
        ;;
    dirs)
        output=$(echo "$changed_files" | while read -r file; do
            if [ -n "$file" ]; then
                dir_path=$(dirname "$file")
                if [ "$FOLDER_DEPTH" -eq 0 ]; then
                    echo "$dir_path"
                else
                    echo "${dir_path//\// }" | cut -d ' ' -f -"${FOLDER_DEPTH}" | tr ' ' '/'
                fi
            fi
        done)
        ;;
    *)
        echo "Error: Unknown output type: $OUTPUT_TYPE" >&2
        exit 1
        ;;
esac

if [ "$OUTPUT_TYPE" = "dirs" ] && ! [[ $FOLDER_DEPTH =~ ^[0-9]+$ ]]; then
    echo "Error: FOLDER_DEPTH must be a positive integer." >&2
    exit 1
fi

if [ "$DEDUP" != "true" ] && [ "$DEDUP" != "false" ]; then
    echo "Error: DEDUP must be either 'true' or 'false'." >&2
    exit 1
fi

if [ "$DEDUP" = "true" ]; then
    output=$(echo "$output" | sort -u)
fi

if ! printf '%s' "$output" > "$OUTPUT_FILE"; then
    echo "Error: Failed to write output to file '$OUTPUT_FILE'." >&2
    exit 1
fi

echo "Output written to $OUTPUT_FILE:"
if ! cat "$OUTPUT_FILE"; then
    echo "Error: Failed to read output from file '$OUTPUT_FILE'." >&2
    exit 1
fi
