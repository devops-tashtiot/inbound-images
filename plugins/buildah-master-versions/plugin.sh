#!/bin/sh

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# buildah-master-versions
#
# Reads tags produced by master-versions plugin, resolves each tag to a Dockerfile, and
# builds + pushes the image via buildah.
#
# Required:
#   PLUGIN_BASE_PATH       — directory to scan for Dockerfiles.
#                       Point to wherever your component folders live.
#                       e.g. "check/plugins" → slug "harel" finds check/plugins/harel/Dockerfile
#                       e.g. "."             → slug "check-plugins-harel" finds check/plugins/harel/Dockerfile
#   PLUGIN_USERNAME   — registry username
#   PLUGIN_PASSWORD   — registry password
#
#   And one of:
#   PLUGIN_TAGS_FILE  — path to newline-separated tag file (e.g. new_tags.txt)
#   PLUGIN_TAGS       — comma or newline-separated tags string
#
# Optional:
#   PLUGIN_REGISTRY        — Docker registry            (default: index.docker.io)
#   PLUGIN_REPO            — image repository/namespace (default: "")
#   PLUGIN_DOCKERFILE      — Dockerfile filename        (default: Dockerfile)
#   PLUGIN_ALIASES         — comma-separated alias tags pushed alongside the version tag.
#                            NOT set by default — only the exact version tag is pushed.
#                            e.g. "latest"       → also push :latest
#                            e.g. "prod,staging" → also push :prod and :staging
#   PLUGIN_TAG_SUFFIX      — appended verbatim to the version before push.
#                            e.g. "-abc123" → :v1.2.0-abc123
#                            e.g. "_rc1"    → :v1.2.0_rc1
#   PLUGIN_DRY_RUN         — "true" → build only, no push
#   PLUGIN_LOG_LEVEL       — buildah log verbosity (default: info)
#                            Available values: panic, fatal, error, warn, info, debug, trace
#   PLUGIN_SKIP_TLS_VERIFY — "true" → --tls-verify=false
#   PLUGIN_INSECURE        — "true" → --tls-verify=false
# ─────────────────────────────────────────────────────────────────────────────

# Print a yellow-coloured message. Accepts >&2 redirect like a normal echo.
yecho() { printf '\033[33m%s\033[0m\n' "$*"; }

validate_required() {
    local missing=""
    [ -z "${PLUGIN_BASE_PATH:-}"     ] && missing="${missing}  PLUGIN_BASE_PATH\n"
    [ -z "${PLUGIN_USERNAME:-}" ] && missing="${missing}  PLUGIN_USERNAME\n"
    [ -z "${PLUGIN_PASSWORD:-}" ] && missing="${missing}  PLUGIN_PASSWORD\n"

    if [ -z "${PLUGIN_TAGS_FILE:-}" ] && [ -z "${PLUGIN_TAGS:-}" ]; then
        missing="${missing}  PLUGIN_TAGS_FILE or PLUGIN_TAGS\n"
    fi

    if [ -n "${missing}" ]; then
        printf 'ERROR: Missing required variables:\n%b' "${missing}" >&2
        exit 1
    fi
}

setup_docker_auth() {
    local registry="${PLUGIN_REGISTRY:-index.docker.io}"
    local tls_verify="true"
    [ "${PLUGIN_SKIP_TLS_VERIFY:-}" = "true" ] && tls_verify="false"
    [ "${PLUGIN_INSECURE:-}"        = "true" ] && tls_verify="false"

    buildah login \
        --username "${PLUGIN_USERNAME}" \
        --password "${PLUGIN_PASSWORD}" \
        --tls-verify="${tls_verify}" \
        "${registry}"
    yecho "Auth configured for ${registry}" >&2
}

# Extract the version: everything after the last '-'.
# nati-1.0.0_abc123   → 1.0.0_abc123
# 1.0.0_abc123        → 1.0.0_abc123  (no '-', whole string is version)
extract_version() {
    local tag="${1}"
    printf '%s' "${tag##*-}"
}

# Extract the slug: everything before the last '-'.
# nati-1.0.0_abc123        → nati
# plugins-docker-1.0.0_abc → plugins-docker
# 1.0.0_abc123             → "" (no '-' before version, root component)
extract_slug() {
    local tag="${1}"
    local ver="${tag##*-}"
    [ "${ver}" = "${tag}" ] && printf '' && return
    local slug="${tag%-${ver}}"
    printf '%s' "${slug%-}"
}


# Given a slug, scan PLUGIN_BASE_PATH for a Dockerfile whose parent directory
# converts to that slug (replacing "/" with "-").
# Empty slug → Dockerfile at PLUGIN_BASE_PATH root itself.
# Prints relative path (e.g. "harel") or "." for root; exits 1 if not found.
find_path_for_slug() {
    local slug="${1}"
    local base="${PLUGIN_BASE_PATH}"
    base="${base%/}"
    local dockerfile_name="${PLUGIN_DOCKERFILE:-Dockerfile}"

    if [ -z "${slug}" ]; then
        if [ -f "${base}/${dockerfile_name}" ] || \
           { [ "${base}" = "." ] && [ -f "${dockerfile_name}" ]; }; then
            printf '.\n'
            return 0
        fi
        return 1
    fi

    while IFS= read -r dockerfile; do
        local dir
        dir="$(dirname "${dockerfile}")"

        local rel
        if [ "${base}" = "." ]; then
            rel="${dir#./}"
        else
            rel="${dir#${base}/}"
        fi

        [ "${rel}" = "${dir}" ] && continue
        [ -z "${rel}"         ] && continue

        local dir_slug
        dir_slug="$(printf '%s' "${rel}" | tr '/' '-')"

        if [ "${dir_slug}" = "${slug}" ]; then
            printf '%s\n' "${rel}"
            return 0
        fi
    done < <(find "${base}" -name "${dockerfile_name}" 2>/dev/null | sort)

    return 1
}

# Build and push one image.
# $1 — rel_path relative to PLUGIN_BASE_PATH ("harel" or "." for root)
# $2 — version as found in the tag (may include "v" prefix)
build_image() {
    local rel_path="${1}"
    local version="${2}"
    local base="${PLUGIN_BASE_PATH}"
    base="${base%/}"
    local registry="${PLUGIN_REGISTRY:-index.docker.io}"
    local repo="${PLUGIN_REPO:-}"
    local dockerfile_name="${PLUGIN_DOCKERFILE:-Dockerfile}"
    local log="${PLUGIN_LOG_LEVEL:-info}"
    local aliases="${PLUGIN_ALIASES:-}"
    local tag_suffix="${PLUGIN_TAG_SUFFIX:-}"

    local tls_verify="true"
    [ "${PLUGIN_SKIP_TLS_VERIFY:-}" = "true" ] && tls_verify="false"
    [ "${PLUGIN_INSECURE:-}"        = "true" ] && tls_verify="false"

    local context
    if [ "${rel_path}" = "." ] || [ -z "${rel_path}" ]; then
        context="${base}"
    elif [ "${base}" = "." ]; then
        context="${rel_path}"
    else
        context="${base}/${rel_path}"
    fi

    local image_base
    if [ "${rel_path}" = "." ] || [ -z "${rel_path}" ]; then
        image_base="${registry}${repo:+/${repo}}"
    else
        image_base="${registry}${repo:+/${repo}}/${rel_path}"
    fi

    local version_tag="${image_base}:${version}${tag_suffix}"

    yecho "================================================================"
    yecho "Component : ${rel_path}"
    yecho "Version   : ${version}${tag_suffix}"
    yecho "Suffix    : ${tag_suffix:-(none)}"
    yecho "Aliases   : ${aliases:-(none)}"
    yecho "Isolation : rootless"
    yecho "Log level : ${log}"
    yecho "Context   : ${context}"
    yecho "Dockerfile: ${context}/${dockerfile_name}"
    yecho ">>> Generated tag : ${version_tag}"
    [ "${PLUGIN_DRY_RUN:-}" = "true" ] && yecho "  (dry-run — build only, no push)"
    yecho "================================================================"

    buildah bud \
        --isolation rootless \
        --log-level "${log}" \
        --file "${context}/${dockerfile_name}" \
        --tag "${version_tag}" \
        "${context}"

    if [ "${PLUGIN_DRY_RUN:-}" = "true" ]; then
        yecho ">>> Dry-run: would push ${version_tag}" >&2
        return 0
    fi

    buildah push --tls-verify="${tls_verify}" "${version_tag}"
    yecho ">>> Pushed: ${version_tag}" >&2

    if [ -n "${aliases}" ]; then
        local alias_tag
        while IFS= read -r alias_tag; do
            alias_tag="$(printf '%s' "${alias_tag}" | tr -d ' ')"
            [ -z "${alias_tag}" ] && continue
            local full_alias="${image_base}:${alias_tag}"
            buildah tag "${version_tag}" "${full_alias}"
            buildah push --tls-verify="${tls_verify}" "${full_alias}"
            yecho ">>> Pushed alias: ${full_alias}" >&2
        done << ALIASES
$(printf '%s' "${aliases}" | tr ',' '\n')
ALIASES
    fi
}

run() {
    local base="${PLUGIN_BASE_PATH}"

    local tags_input=""
    if [ -n "${PLUGIN_TAGS_FILE:-}" ]; then
        if [ ! -f "${PLUGIN_TAGS_FILE}" ]; then
            yecho "Tags file '${PLUGIN_TAGS_FILE}' does not exist — nothing to build." >&2
            return 0
        fi
        if [ ! -s "${PLUGIN_TAGS_FILE}" ]; then
            yecho "Tags file '${PLUGIN_TAGS_FILE}' is empty — nothing to build." >&2
            return 0
        fi
        yecho "Reading tags from file: ${PLUGIN_TAGS_FILE}" >&2
        tags_input="$(cat "${PLUGIN_TAGS_FILE}")"
    else
        yecho "Reading tags from PLUGIN_TAGS env var" >&2
        tags_input="$(printf '%s' "${PLUGIN_TAGS}" | tr ',' '\n')"
    fi

    if [ -z "${tags_input}" ]; then
        yecho "Tag list is empty. Nothing to build." >&2
        return 0
    fi

    printf '%s\n' "${tags_input}" | while IFS= read -r tag; do
        [ -z "${tag}" ] && continue
        yecho "--- Processing tag: ${tag} ---" >&2

        local version
        version="$(extract_version "${tag}")"
        if [ -z "${version}" ]; then
            yecho "WARN: Cannot extract a version from '${tag}'. Skipping." >&2
            continue
        fi

        local slug
        slug="$(extract_slug "${tag}")"
        yecho "slug='${slug}'  version='${version}'" >&2

        local rel_path
        if ! rel_path="$(find_path_for_slug "${slug}")"; then
            if [ -z "${slug}" ]; then
                yecho "WARN: Tag '${tag}' . Expected a ${PLUGIN_DOCKERFILE:-Dockerfile} at the root of '${base}/' but none was found. Skipping." >&2
            else
                yecho "WARN: Tag '${tag}' maps to component '${slug}' but no ${PLUGIN_DOCKERFILE:-Dockerfile} was found under '${base}/${slug}/'. Skipping." >&2
            fi
            continue
        fi
        yecho "resolved path='${rel_path}'" >&2

        build_image "${rel_path}" "${version}"
    done
}

main() {
    yecho "--- BUILDAH MASTER VERSIONS PLUGIN START ---" >&2
    validate_required
    setup_docker_auth
    run
    yecho "--- BUILDAH MASTER VERSIONS PLUGIN DONE ---" >&2
}

main
