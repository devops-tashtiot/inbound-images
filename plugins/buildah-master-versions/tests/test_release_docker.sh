#!/usr/bin/env bash
# =============================================================================
# Unit tests for plugins/buildah-master-versions/plugin.sh
#
# Strategy
# --------
# No Docker, no buildah daemon, no registry required.
#
# 1. Functions are loaded by sourcing plugin.sh through bash (stripping the
#    busybox shebang on line 1 and the bare `main` call at EOF so main() is
#    not triggered on source).
#
# 2. build_image() is replaced with a mock that appends one line per call to
#    a temp file (BUILD_LOG).  Writing to a file — not a variable — is
#    essential because run() pipes tags through a `while` loop, which runs in
#    a subshell.  Variable writes from a subshell are invisible to the parent;
#    file writes persist.
#
# 3. A temp workspace (TMPWS) is created from scratch with minimal mock
#    Dockerfiles.  No existing repo files or folders are used — everything is
#    created fresh every run and cleaned up on exit.
#
# Usage:
#   cd plugins/buildah-master-versions && ./test_release_docker.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SH="${SCRIPT_DIR}/../plugin.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Mock Dockerfile base image.
# Change this single line to switch the base image used in all mock Dockerfiles.
# ─────────────────────────────────────────────────────────────────────────────
MOCK_BASE_IMAGE="alpine:3.19"

PASS=0
FAIL=0

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
ok()   { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; echo "        $2"; FAIL=$((FAIL + 1)); }

assert_eq() {
    local name="$1" got="$2" want="$3"
    if [ "${got}" = "${want}" ]; then
        ok "${name}"
    else
        fail "${name}" "got='${got}'  want='${want}'"
    fi
}

assert_contains() {
    local name="$1" haystack="$2" needle="$3"
    if printf '%s' "${haystack}" | grep -qF "${needle}"; then
        ok "${name}"
    else
        fail "${name}" "expected to find: '${needle}'"
    fi
}

assert_not_contains() {
    local name="$1" haystack="$2" needle="$3"
    if ! printf '%s' "${haystack}" | grep -qF "${needle}"; then
        ok "${name}"
    else
        fail "${name}" "expected NOT to find: '${needle}'"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Load plugin functions
#
# sed '1d'         removes the shebang (#!/busybox/busybox sh) so bash does
#                  not try to execute busybox.
# sed '/^main$/d'  removes the bare `main` call on the last line so sourcing
#                  does not trigger the full plugin entrypoint.
# All function definitions (extract_version, extract_slug, find_path_for_slug,
# validate_required, setup_docker_auth, build_image, run, main) are loaded
# into the current shell.  We then override build_image below.
# ─────────────────────────────────────────────────────────────────────────────
# shellcheck disable=SC1090
source <(sed '1d;/^main$/d' "${PLUGIN_SH}")

# ─────────────────────────────────────────────────────────────────────────────
# Temp workspace — created entirely from scratch, no repo files used.
#
#   TMPWS/
#     Dockerfile                               <- root image  (slug = "")
#     plugins/
#       master-versions/Dockerfile             <- slug  plugins-master-versions
#       buildah-master-versions/Dockerfile      <- slug  plugins-buildah-master-versions
#     standalone/Dockerfile                    <- slug  standalone
#     deep/nested/component/Dockerfile         <- slug  deep-nested-component
# ─────────────────────────────────────────────────────────────────────────────
TMPWS="$(mktemp -d)"
BUILD_LOG="$(mktemp)"
trap 'rm -rf "${TMPWS}" "${BUILD_LOG}"' EXIT

_mk_dockerfile() {
    # Creates a minimal valid Dockerfile at the given absolute path.
    # Uses MOCK_BASE_IMAGE so the base image is configurable in one place.
    mkdir -p "$(dirname "$1")"
    printf 'FROM %s\nLABEL mock="%s"\n' "${MOCK_BASE_IMAGE}" "$1" > "$1"
}

_mk_dockerfile "${TMPWS}/Dockerfile"
_mk_dockerfile "${TMPWS}/plugins/master-versions/Dockerfile"
_mk_dockerfile "${TMPWS}/plugins/buildah-master-versions/Dockerfile"
_mk_dockerfile "${TMPWS}/standalone/Dockerfile"
_mk_dockerfile "${TMPWS}/deep/nested/component/Dockerfile"

# ─────────────────────────────────────────────────────────────────────────────
# Mock build_image
#
# Replaces the real build_image (which calls buildah bud / buildah push) with a stub
# that records each call as one line in BUILD_LOG:
#   BUILD:path=<rel_path>,ver=<version>
#
# A file is used instead of a variable because run() feeds tags through a
# pipe (`printf | while read`), which bash runs in a subshell.  Variable
# mutations inside a subshell die with it; file writes survive.
# ─────────────────────────────────────────────────────────────────────────────
build_image() {
    printf 'BUILD:path=%s,ver=%s\n' "${1}" "${2}" >> "${BUILD_LOG}"
}


# =============================================================================
# SECTION 1: extract_version
#
# Pulls the version suffix from the right end of a tag using the regex:
#   (v?[0-9]+(\.[0-9]+)*)$
# The slug before it is irrelevant — only the trailing numeric segment matters.
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 1: extract_version"
echo "└────────────────────────────────────────────"

# 1.1 — Flat slug, no v-prefix.
# Baseline: one path component, plain semver after a single hyphen.
assert_eq "1.1 flat slug no v-prefix" \
    "$(extract_version 'nati-1.0.0')" "1.0.0"

# 1.2 — Flat slug, v-prefix present.
# The v must be included in the returned string because buildah uses it
# verbatim as the image tag.  Stripping it would push :2.3.1 instead of :v2.3.1.
assert_eq "1.2 flat slug v-prefix preserved" \
    "$(extract_version 'nati-v2.3.1')" "v2.3.1"

# 1.3 — Nested slug, no v-prefix (the main case).
# plugins-master-versions-1.0.0 has multiple hyphens; the version is always
# the trailing numeric segment regardless of how many hyphens precede it.
assert_eq "1.3 nested slug no v-prefix" \
    "$(extract_version 'plugins-master-versions-1.0.0')" "1.0.0"

# 1.4 — Nested slug with v-prefix.
assert_eq "1.4 nested slug v-prefix" \
    "$(extract_version 'plugins-buildah-master-versions-v2.1.3')" "v2.1.3"

# 1.5 — Version-only tag (root release, no component prefix).
# Empty brackets [] in master-versions produce a tag like "1.0.0" with no slug.
assert_eq "1.5 version-only tag" \
    "$(extract_version '1.0.0')" "1.0.0"

# 1.6 — No version at all.
# Must return empty string, not crash.  Callers treat empty as "skip this tag".
assert_eq "1.6 no version returns empty" \
    "$(extract_version 'notaversion')" ""

echo ""


# =============================================================================
# SECTION 2: extract_slug
#
# Returns everything BEFORE the trailing -<version> segment.
# If the whole tag IS the version (root release), returns empty string.
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 2: extract_slug"
echo "└────────────────────────────────────────────"

# 2.1 — Flat slug, no v-prefix.
assert_eq "2.1 flat slug" \
    "$(extract_slug 'nati-1.0.0')" "nati"

# 2.2 — Nested slug, no v-prefix (main case).
# plugins-master-versions-1.0.0 must yield "plugins-master-versions" so
# find_path_for_slug can later resolve it to plugins/master-versions/.
assert_eq "2.2 nested slug plugins-master-versions" \
    "$(extract_slug 'plugins-master-versions-1.0.0')" "plugins-master-versions"

# 2.3 — Nested slug with v-prefix.
# The v must not bleed into the slug; only the numeric part is the version.
assert_eq "2.3 nested slug v-prefix does not bleed into slug" \
    "$(extract_slug 'plugins-buildah-master-versions-v2.1.3')" "plugins-buildah-master-versions"

# 2.4 — Version-only tag → empty slug.
# When master-versions releases the repo root (location ""), the tag has no
# prefix.  Empty slug tells find_path_for_slug to look at PLUGIN_BASE_PATH root.
assert_eq "2.4 version-only tag gives empty slug" \
    "$(extract_slug '1.0.0')" ""

# 2.5 — v-prefix version-only tag → empty slug.
assert_eq "2.5 v-prefix version-only tag gives empty slug" \
    "$(extract_slug 'v1.0.0')" ""

echo ""


# =============================================================================
# SECTION 3: find_path_for_slug — PLUGIN_BASE_PATH = repo root (TMPWS)
#
# How the reverse lookup works:
#   find PLUGIN_BASE_PATH -name Dockerfile | sort
#   for each found file:
#     rel  = parent_dir stripped of PLUGIN_BASE_PATH prefix
#     slug = rel with every / replaced by -
#     if slug == target_slug -> return rel
#
# With PLUGIN_BASE_PATH at the repo root, the full directory path contributes to
# the slug: plugins/master-versions -> slug plugins-master-versions.
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 3: find_path_for_slug (PLUGIN_BASE_PATH = repo root)"
echo "└────────────────────────────────────────────"

export PLUGIN_BASE_PATH="${TMPWS}"

# 3.1 — Core case: PLUGIN_BASE_PATH=. and tag plugins-master-versions-1.0.0.
# find scans TMPWS, finds TMPWS/plugins/master-versions/Dockerfile,
# strips TMPWS/ -> rel=plugins/master-versions -> slug=plugins-master-versions -> match.
assert_eq "3.1 plugins-master-versions -> plugins/master-versions" \
    "$(find_path_for_slug 'plugins-master-versions')" "plugins/master-versions"

# 3.2 — Same logic for the buildah plugin.
assert_eq "3.2 plugins-buildah-master-versions -> plugins/buildah-master-versions" \
    "$(find_path_for_slug 'plugins-buildah-master-versions')" "plugins/buildah-master-versions"

# 3.3 — Flat slug at repo root level.
assert_eq "3.3 standalone -> standalone" \
    "$(find_path_for_slug 'standalone')" "standalone"

# 3.4 — Deeply nested path.
# deep/nested/component -> slug deep-nested-component via tr '/' '-'.
assert_eq "3.4 deep-nested-component -> deep/nested/component" \
    "$(find_path_for_slug 'deep-nested-component')" "deep/nested/component"

# 3.5 — Empty slug = root Dockerfile.
# The function short-circuits for empty slug: checks for PLUGIN_BASE_PATH/Dockerfile
# directly and returns "." without scanning the whole tree.
assert_eq "3.5 empty slug finds root Dockerfile" \
    "$(find_path_for_slug '')" "."

# 3.6 — Unknown slug must return exit 1, not silently return a wrong path.
# Callers use `if ! rel_path=$(find_path_for_slug ...); then WARN; continue`.
# A silent success here would build the wrong image without any indication.
if find_path_for_slug 'ghost-service' 2>/dev/null; then
    fail "3.6 unknown slug returns non-zero" "expected exit 1 for non-existent slug"
else
    ok "3.6 unknown slug returns exit 1"
fi

echo ""


# =============================================================================
# SECTION 4: find_path_for_slug — PLUGIN_BASE_PATH = TMPWS/plugins
#
# Shifting PLUGIN_BASE_PATH one level deeper shortens slugs.  The same Dockerfiles
# are on disk but rel is now computed relative to TMPWS/plugins/:
#   plugins/master-versions/Dockerfile -> rel=master-versions -> slug=master-versions
#
# Main case: PLUGIN_BASE_PATH=plugins + tag master-versions-1.0.0 -> master-versions/
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 4: find_path_for_slug (PLUGIN_BASE_PATH = plugins/)"
echo "└────────────────────────────────────────────"

export PLUGIN_BASE_PATH="${TMPWS}/plugins"

# 4.1 — Short slug resolves with shallow base.
assert_eq "4.1 master-versions -> master-versions (base=plugins/)" \
    "$(find_path_for_slug 'master-versions')" "master-versions"

# 4.2 — Same for buildah variant.
assert_eq "4.2 buildah-master-versions -> buildah-master-versions (base=plugins/)" \
    "$(find_path_for_slug 'buildah-master-versions')" "buildah-master-versions"

# 4.3 — Full slug (plugins-master-versions) must NOT match under plugins/ base.
# Proves that changing PLUGIN_BASE_PATH actually changes the slug namespace and a
# tag generated with one base cannot silently resolve under a different base.
if find_path_for_slug 'plugins-master-versions' 2>/dev/null; then
    fail "4.3 full slug must not resolve under plugins/ base" "expected exit 1"
else
    ok "4.3 full slug plugins-master-versions not found under plugins/ base"
fi

echo ""


# =============================================================================
# SECTION 5: run() routing — PLUGIN_BASE_PATH = repo root
#
# Tests the full pipeline: tag string -> extract_version + extract_slug ->
# find_path_for_slug -> build_image call.
# build_image is mocked; each call appends one line to BUILD_LOG.
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 5: run() routing (PLUGIN_BASE_PATH = repo root)"
echo "└────────────────────────────────────────────"

export PLUGIN_BASE_PATH="${TMPWS}"
export PLUGIN_USERNAME="user"
export PLUGIN_PASSWORD="pass"
unset PLUGIN_TAGS_FILE 2>/dev/null || true
unset PLUGIN_DRY_RUN  2>/dev/null || true

# 5.1 — Core case: nested slug, no v-prefix.
# Verifies the full chain works end-to-end: slug extracted, path found by
# filesystem scan, build_image called with the correct arguments.
> "${BUILD_LOG}"
export PLUGIN_TAGS="plugins-master-versions-1.0.0"
run 2>/dev/null
assert_contains "5.1 plugins-master-versions-1.0.0 routed correctly" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=plugins/master-versions,ver=1.0.0"

# 5.2 — Nested slug with v-prefix.
# The v must survive all the way to build_image so the image is pushed
# as :v2.1.3, not :2.1.3.
> "${BUILD_LOG}"
export PLUGIN_TAGS="plugins-buildah-master-versions-v2.1.3"
run 2>/dev/null
assert_contains "5.2 v-prefix preserved through full pipeline" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=plugins/buildah-master-versions,ver=v2.1.3"

# 5.3 — Flat slug at repo root level.
> "${BUILD_LOG}"
export PLUGIN_TAGS="standalone-2.5.0"
run 2>/dev/null
assert_contains "5.3 standalone-2.5.0 routed" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=standalone,ver=2.5.0"

# 5.4 — Unknown slug: must WARN and skip, not crash or silently build a wrong image.
# find_path_for_slug returns exit 1; run() prints WARN to stderr and moves on.
> "${BUILD_LOG}"
export PLUGIN_TAGS="ghost-1.0.0"
out="$(run 2>&1 || true)"
assert_eq       "5.4 unknown slug: no build call"  "$(cat "${BUILD_LOG}")" ""
assert_contains "5.4 unknown slug: WARN in output" "${out}" "WARN"

# 5.5 — Comma-separated multi-tag string.
# run() converts commas to newlines and processes each tag independently.
> "${BUILD_LOG}"
export PLUGIN_TAGS="plugins-master-versions-1.0.0,standalone-2.5.0"
run 2>/dev/null
assert_contains "5.5 multi-tag: master-versions built" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=plugins/master-versions,ver=1.0.0"
assert_contains "5.5 multi-tag: standalone built" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=standalone,ver=2.5.0"

# 5.6 — Empty PLUGIN_TAGS: nothing built, no crash.
# Normal situation when no components were released in the current CI run.
> "${BUILD_LOG}"
export PLUGIN_TAGS=""
run 2>/dev/null || true
assert_eq "5.6 empty tags: no builds" "$(cat "${BUILD_LOG}")" ""

# 5.7 — Version-only tag (root Dockerfile).
# master-versions releases the repo root with location ""; tag is just "1.0.0".
# find_path_for_slug("") returns "." so build_image gets path=. and ver=1.0.0.
> "${BUILD_LOG}"
export PLUGIN_TAGS="1.0.0"
run 2>/dev/null
assert_contains "5.7 version-only tag routes to root Dockerfile" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=.,ver=1.0.0"

echo ""


# =============================================================================
# SECTION 6: run() routing — PLUGIN_BASE_PATH = plugins/
#
# Same Dockerfiles; base is one level deeper.
# Tags are now shorter: master-versions-1.0.0 instead of
# plugins-master-versions-1.0.0.
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 6: run() routing (PLUGIN_BASE_PATH = plugins/)"
echo "└────────────────────────────────────────────"

export PLUGIN_BASE_PATH="${TMPWS}/plugins"
unset PLUGIN_TAGS_FILE 2>/dev/null || true

# 6.1 — Short tag + shallow base end-to-end.
# With PLUGIN_BASE_PATH=plugins, master-versions-1.0.0 maps to master-versions/
# instead of plugins/master-versions/.
> "${BUILD_LOG}"
export PLUGIN_TAGS="master-versions-1.0.0"
run 2>/dev/null
assert_contains "6.1 master-versions-1.0.0 with base=plugins/" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=master-versions,ver=1.0.0"

# 6.2 — buildah-master-versions via shallow base.
> "${BUILD_LOG}"
export PLUGIN_TAGS="buildah-master-versions-3.0.0"
run 2>/dev/null
assert_contains "6.2 buildah-master-versions-3.0.0 with base=plugins/" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=buildah-master-versions,ver=3.0.0"

# 6.3 — Full slug with wrong base must WARN, not build anything.
# If a tag was generated with PLUGIN_BASE_PATH=. but the plugin runs with
# PLUGIN_BASE_PATH=plugins, the lookup fails safely instead of building silently.
> "${BUILD_LOG}"
export PLUGIN_TAGS="plugins-master-versions-1.0.0"
out="$(run 2>&1 || true)"
assert_eq       "6.3 full slug under plugins/ base: no build" "$(cat "${BUILD_LOG}")" ""
assert_contains "6.3 full slug under plugins/ base: WARN"     "${out}" "WARN"

echo ""


# =============================================================================
# SECTION 7: PLUGIN_TAGS_FILE
#
# run() checks PLUGIN_TAGS_FILE first; if the file exists it reads tags from
# there and ignores PLUGIN_TAGS entirely.
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 7: PLUGIN_TAGS_FILE"
echo "└────────────────────────────────────────────"

export PLUGIN_BASE_PATH="${TMPWS}"
TAGS_FILE="$(mktemp)"

# 7.1 — Single tag from file.
# Verifies that file reading works before testing multi-tag behaviour.
printf 'plugins-master-versions-3.0.0\n' > "${TAGS_FILE}"
> "${BUILD_LOG}"
export PLUGIN_TAGS_FILE="${TAGS_FILE}"
export PLUGIN_TAGS=""
run 2>/dev/null
assert_contains "7.1 single tag read from file" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=plugins/master-versions,ver=3.0.0"

# 7.2 — Newline-separated multi-tag file.
# master-versions appends one tag per line to the file; all must be built.
printf 'plugins-master-versions-3.0.0\nstandalone-1.0.0\n' > "${TAGS_FILE}"
> "${BUILD_LOG}"
run 2>/dev/null
assert_contains "7.2 file multi-tag: master-versions built" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=plugins/master-versions,ver=3.0.0"
assert_contains "7.2 file multi-tag: standalone built" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=standalone,ver=1.0.0"

# 7.3 — PLUGIN_TAGS_FILE takes priority over PLUGIN_TAGS.
# If both are set, only the file is used.  Prevents processing stale tags
# left in the env var when a file is also provided.
printf 'standalone-5.0.0\n' > "${TAGS_FILE}"
> "${BUILD_LOG}"
export PLUGIN_TAGS="plugins-master-versions-9.9.9"
run 2>/dev/null
assert_contains     "7.3 file wins over PLUGIN_TAGS" \
    "$(cat "${BUILD_LOG}")" "BUILD:path=standalone,ver=5.0.0"
assert_not_contains "7.3 PLUGIN_TAGS ignored when file is set" \
    "$(cat "${BUILD_LOG}")" "ver=9.9.9"

rm -f "${TAGS_FILE}"
unset PLUGIN_TAGS_FILE

echo ""


# =============================================================================
# SECTION 8: validate_required
#
# validate_required() prints to stderr and calls exit 1 when a required
# variable is missing.  Each call here runs inside $( () ) — an explicit
# subshell inside a command substitution — so its exit 1 kills only the
# subshell, not the test script.  stderr is redirected to stdout so the
# error message can be inspected.
# =============================================================================
echo "┌────────────────────────────────────────────"
echo "│ SECTION 8: validate_required"
echo "└────────────────────────────────────────────"

# 8.1 — Missing PLUGIN_BASE_PATH.
# Error message must name the missing variable so the user knows what to fix.
out="$( (unset PLUGIN_BASE_PATH; export PLUGIN_USERNAME="u"; export PLUGIN_PASSWORD="p";
          export PLUGIN_TAGS="x"; validate_required) 2>&1 || true)"
assert_contains "8.1 missing PLUGIN_BASE_PATH: error names PLUGIN_BASE_PATH" "${out}" "PLUGIN_BASE_PATH"

# 8.2 — Missing PLUGIN_USERNAME.
out="$( (export PLUGIN_BASE_PATH="."; unset PLUGIN_USERNAME; export PLUGIN_PASSWORD="p";
          export PLUGIN_TAGS="x"; validate_required) 2>&1 || true)"
assert_contains "8.2 missing PLUGIN_USERNAME: error names PLUGIN_USERNAME" "${out}" "PLUGIN_USERNAME"

# 8.2b — Missing PLUGIN_PASSWORD.
# Without a password the docker auth base64 encoding has nothing to encode
# and the registry push would fail with an authentication error.
out="$( (export PLUGIN_BASE_PATH="."; export PLUGIN_USERNAME="u"; unset PLUGIN_PASSWORD;
          export PLUGIN_TAGS="x"; validate_required) 2>&1 || true)"
assert_contains "8.2b missing PLUGIN_PASSWORD: error names PLUGIN_PASSWORD" "${out}" "PLUGIN_PASSWORD"

# 8.3 — Neither PLUGIN_TAGS nor PLUGIN_TAGS_FILE provided.
# Either one is acceptable; if both are absent validation must fail.
out="$( (export PLUGIN_BASE_PATH="."; export PLUGIN_USERNAME="u"; export PLUGIN_PASSWORD="p";
          unset PLUGIN_TAGS; unset PLUGIN_TAGS_FILE; validate_required) 2>&1 || true)"
assert_contains "8.3 missing both tags vars: error names PLUGIN_TAGS" "${out}" "PLUGIN_TAGS"

# 8.4 — All required vars present → must exit 0 (no false positives).
if ( export PLUGIN_BASE_PATH="."; export PLUGIN_USERNAME="u"; export PLUGIN_PASSWORD="p"; \
     export PLUGIN_TAGS="x"; unset PLUGIN_TAGS_FILE; validate_required ) 2>/dev/null; then
    ok "8.4 all required vars set: validate_required exits 0"
else
    fail "8.4 all required vars set: expected exit 0" "exited non-zero"
fi

echo ""


# =============================================================================
# Summary
# =============================================================================
echo "════════════════════════════════════════════"
printf ' Results: %d passed, %d failed\n' "${PASS}" "${FAIL}"
echo "════════════════════════════════════════════"

[ "${FAIL}" -eq 0 ]
