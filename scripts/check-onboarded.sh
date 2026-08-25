#!/bin/sh
# check-onboarded.sh — informational, non-blocking: flags any component with a
# Dockerfile but no VERSION.txt yet. Not wrong by itself (a component mid-development
# legitimately might not be released yet) — but it means `[**]` (used for CA rotations)
# silently will NOT reach it until it gets one explicit, by-name first release. See
# README "`[**]` does NOT reach a component's first release" for why. Surfaced here so
# that gap stays visible instead of only being discovered the hard way, during an
# actual rotation.
set -eu

found=0
for dockerfile in base/*/Dockerfile plugins/*/Dockerfile; do
    [ -f "$dockerfile" ] || continue
    dir="$(dirname "$dockerfile")"
    if [ ! -f "$dir/VERSION.txt" ]; then
        echo "NOT ONBOARDED: $dir has a Dockerfile but no VERSION.txt yet."
        echo "               [**] will skip it on the next CA rotation until it gets:"
        echo "               feat[$dir]: initial release"
        found=1
    fi
done

if [ "$found" = 0 ]; then
    echo "ok: every component with a Dockerfile has a VERSION.txt — [**] reaches all of them."
fi

exit 0
