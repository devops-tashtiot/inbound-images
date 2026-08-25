import os
import re
import shlex
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from urllib.request import urlopen, Request

# ── Known source aliases for generated (hardened) Dockerfiles ────────────────
# The first path segment of a location like "dockerhub/woodpeckerci/plugin-git/2.9.3"
# — an organizing subpath under PLUGIN_MIRROR_REGISTRY, not a registry host of its
# own (every alias resolves to the same mirror project). Add new sources here.
_KNOWN_SOURCE_ALIASES = {"dockerhub", "redhat", "codeberg", "quay", "ghcr"}


def load_cliff_parsers(toml_path):
    """
    Reads cliff.toml and extracts commit_parsers and bump config.

    Returns (parsers, bump_cfg) where:
      parsers  = [{"message": str, "group": str, "bump_type": str, "skip": bool}, ...]
      bump_cfg = {"features_always_bump_minor": bool,
                  "breakage_always_bump_major": bool,
                  "custom_major_increment_regex": str|None}

    Parsers are ordered — first match wins (same as git-cliff).
    Returns ([], {}) on any read/parse error.
    """
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return [], {}

    raw_parsers = data.get("git", {}).get("commit_parsers", [])
    parsers = [
        {
            "message":   p.get("message", ""),
            "group":     p.get("group", ""),
            "bump_type": p.get("bump_type", ""),
            "skip":      bool(p.get("skip", False)),
        }
        for p in raw_parsers
        if "message" in p
    ]

    raw_bump = data.get("bump", {})
    bump_cfg = {
        "features_always_bump_minor":   raw_bump.get("features_always_bump_minor",  None),
        "breakage_always_bump_major":   raw_bump.get("breakage_always_bump_major",  None),
        "custom_major_increment_regex": raw_bump.get("custom_major_increment_regex", None),
        "custom_minor_increment_regex": raw_bump.get("custom_minor_increment_regex", None),
    }

    return parsers, bump_cfg


def run_command(command):
    """Executes shell commands and captures output."""
    return subprocess.run(command, shell=True, capture_output=True, text=True)


def _normalize_changelog_levels(changelog_level):
    """
    Normalize the changelog-level spec into a set of allowed integer depths, or None.

    Accepts:
      None                   -> None (no depth enforcement)
      an int (e.g. 2)        -> {2}
      a str "2"              -> {2}
      a comma-separated str  -> {2, 3, 4}   (e.g. "2,3,4" or "2, 3, 4")
      an iterable of ints    -> set(...)

    Raises ValueError if any value is not a non-negative integer, or if a
    string/iterable spec yields no usable numbers.
    """
    if changelog_level is None:
        return None
    if isinstance(changelog_level, bool):
        raise ValueError("changelog level must be an integer, not a bool")
    if isinstance(changelog_level, int):
        values = [changelog_level]
    elif isinstance(changelog_level, str):
        values = [int(p.strip()) for p in changelog_level.split(",") if p.strip() != ""]
        if not values:
            raise ValueError("no changelog levels parsed from string")
    else:
        values = [int(x) for x in changelog_level]
        if not values:
            raise ValueError("no changelog levels provided")
    levels = set(values)
    if any(l < 0 for l in levels):
        raise ValueError("changelog levels must be non-negative")
    return levels


def parse_pr_body(body, parsers=None, changelog_level=None):
    """
    Parses PR body lines matching: type[loc1, loc2]!: description

    Returns dict[location -> set[commit_str]] where:
      - location is a path string:
          ""          -> root (VERSION.txt/CHANGELOG.md at PLUGIN_BASE_PATH)
          "nati"      -> nati/
          "base/argo" -> base/argo/
      - commit_str is the line with [locations] removed — passed as-is to git-cliff.
          feat[nati]!: add login  ->  feat!: add login
          feat[nati]: add login          ->  feat: add login

    changelog_level (int | str | Iterable[int] | None):
      Enforces the expected depth of every location in the PR body. Depth is
      the number of path segments (root = 0, "nati" = 1, "plugins/docker" = 2, ...).
      A location is accepted iff its depth is one of the allowed depths.

    Multi-line blocks:
      After bracket removal, all following lines are collected as continuation
      until the next commit-pattern line. Blank lines are included.

    Empty [] means root location (empty string key in the returned dict).
    """
    if parsers is None:
        parsers = []

    if not parsers:
        print(">>> ERROR: cliff.toml commit_parsers has no valid message patterns — cannot parse commits.")
        return {}

    levels = _normalize_changelog_levels(changelog_level)

    bracket_re = re.compile(r'\[([^[\]]*)\]')

    def _match_line(current_line):
        for p in parsers:
            msg = p.get("message", "")
            if not msg:
                continue
            pattern_match = re.match(msg, current_line)
            if pattern_match and current_line[pattern_match.end():pattern_match.end()+1] == '[':
                bracket_match = bracket_re.match(current_line, pattern_match.end())
                if bracket_match and re.match(r'^!?:', current_line[bracket_match.end():]):
                    return bracket_match
        return None

    def _matches_level(location):
        if levels is None:
            return True
        if "*" in location:
            # A wildcard token has no depth of its own — [*], [base/*], [**]
            # and [base/**] all expand (in _expand_locations) to concrete
            # locations of whatever depth they actually are; the literal
            # wildcard string itself isn't a location to depth-gate.
            return True
        if location == "":
            return 0 in levels
        if "" in location.split("/"):
            return False
        return (location.count("/") + 1) in levels

    result = {}
    lines = body.splitlines() if body else []
    i = 0

    while i < len(lines):
        current_line = lines[i]
        bracket_match = _match_line(current_line)

        if bracket_match:
            raw_locs  = bracket_match.group(1)
            locations = [loc.strip() for loc in raw_locs.split(",")]

            failed = [loc for loc in locations if not _matches_level(loc)]
            if failed:
                def _level_reason(loc):
                    allowed = ",".join(str(l) for l in sorted(levels))
                    if loc == "":
                        return (f"'' — empty location (depth 0), not in allowed "
                                f"PLUGIN_CHANGELOG_LEVEL depth(s) [{allowed}]")
                    segments = loc.split("/")
                    if "" in segments:
                        if loc.startswith("/"):
                            return f"'{loc}' — leading slash, missing component name before first '/'"
                        if loc.endswith("/"):
                            return f"'{loc}' — trailing slash, missing component name after last '/'"
                        return f"'{loc}' — consecutive '//', missing component name between slashes"
                    actual_depth = loc.count("/") + 1
                    return (
                        f"'{loc}' — wrong depth: {actual_depth} path segment(s), "
                        f"but PLUGIN_CHANGELOG_LEVEL allows depth(s) [{allowed}]"
                    )

                reasons = "; ".join(_level_reason(loc) for loc in failed)
                print(f">>> SKIP: '{current_line.rstrip()}' — {reasons}")
                i += 1
                continue

            commit_str = current_line[:bracket_match.start()] + current_line[bracket_match.end():]
            loc_display = ", ".join(f"'{loc}'" if loc else "''" for loc in locations)
            print(f">>> ACCEPT: '{current_line.rstrip()}' — location(s) {loc_display}")

            i += 1
            continuation = []
            while i < len(lines):
                if _match_line(lines[i]):
                    break
                continuation.append(lines[i])
                if lines[i].strip():
                    print(f">>> CONTINUATION: '{lines[i].rstrip()}' — body of above commit")
                i += 1
            if continuation:
                commit_str = commit_str.rstrip() + "\n" + "\n".join(continuation)

            for loc in locations:
                result.setdefault(loc, set()).add(commit_str)
        else:
            if current_line.strip():
                print(f">>> IGNORED: '{current_line.rstrip()}' — does not match any commit pattern")
            i += 1

    return result


def _all_versioned_dirs(scan_root, prefix=""):
    """Recursively find every dir under scan_root that has its own VERSION.txt,
    returning paths relative to the original root (prefix-joined). Used by the
    [**] / [<prefix>/**] wildcards — the only way to reach arbitrarily deep
    locations (e.g. a 4-level hardened image path) in one selector."""
    found = []
    try:
        entries = sorted(os.listdir(scan_root))
    except OSError:
        return found
    for e in entries:
        full = os.path.join(scan_root, e)
        if not os.path.isdir(full):
            continue
        rel = f"{prefix}/{e}" if prefix else e
        if os.path.exists(os.path.join(full, "VERSION.txt")):
            found.append(rel)
        found.extend(_all_versioned_dirs(full, rel))
    return found


def _expand_locations(location_to_commits, root_path, exclude_regex="", include_root_in_double_star=False):
    """
    Expands wildcard locations and applies SCOPE_EXCLUDE_REGEX.

    Wildcard rules:
      [*]        -> all direct subdirs of root_path
      [base/*]   -> all subdirs of root_path/base/
      [**]       -> every dir anywhere under root_path that has its own VERSION.txt
                    (+ root itself, when include_root_in_double_star is True —
                    a CA-managed repo's root VERSION.txt IS the CA version, so
                    "everything" naturally includes it: one `feat[**]:` line
                    rotates root and every declared image, no separate `[]`
                    line needed)
      [base/**]  -> same, restricted to under root_path/base/ (root is never
                    implied by a scoped `**`, only the bare `[**]`)
      [""]       -> root, passes through as-is

    SCOPE_EXCLUDE_REGEX is applied to ALL locations including root ("").
    Returns new dict with wildcards replaced by concrete locations.
    If there are no wildcards and no exclusions, returns the original dict unchanged.
    """
    has_wildcard = any("*" in loc for loc in location_to_commits)
    has_exclude = bool(exclude_regex)

    if not has_wildcard and not has_exclude:
        return location_to_commits

    result = {}

    for loc, commits in location_to_commits.items():
        subdirs = None

        if loc == "**":
            subdirs = _all_versioned_dirs(root_path)
            if include_root_in_double_star:
                subdirs = [""] + subdirs
        elif loc.endswith("/**"):
            parent = loc[:-3]
            subdirs = _all_versioned_dirs(os.path.join(root_path, parent), parent)
        elif loc == "*":
            try:
                subdirs = sorted(
                    e for e in os.listdir(root_path)
                    if os.path.isdir(os.path.join(root_path, e))
                )
            except OSError:
                subdirs = []
        elif loc.endswith("/*"):
            parent = loc[:-2]
            scan_dir = os.path.join(root_path, parent)
            try:
                subdirs = sorted(
                    f"{parent}/{e}" for e in os.listdir(scan_dir)
                    if os.path.isdir(os.path.join(scan_dir, e))
                )
            except OSError:
                subdirs = []

        if subdirs is not None:
            for subdir in subdirs:
                if exclude_regex and re.search(exclude_regex, subdir):
                    print(f"\033[33m    >>> SKIP: location '{subdir}' excluded by SCOPE_EXCLUDE_REGEX\033[0m")
                    continue
                result.setdefault(subdir, set()).update(commits)
        else:
            # Explicit location (including "" for root)
            if exclude_regex and re.search(exclude_regex, loc):
                display = loc if loc else ""
                print(f"\033[33m    >>> SKIP: location '{display}' excluded by SCOPE_EXCLUDE_REGEX\033[0m")
                continue
            result.setdefault(loc, set()).update(commits)

    return result


def _bump_subject(commit):
    """
    Subject line used for bump-level detection. If the subject has no
    description after the type/colon (e.g. 'feat:' with the real text on
    the next line), the first non-blank continuation line is folded in
    instead — otherwise git-cliff sees a bare 'feat:' and silently falls
    back to a patch bump. A subject that already has real text after the
    colon (e.g. 'feat: real subject') is left as-is, unchanged.
    """
    lines = commit.splitlines()
    subject = lines[0]
    if re.search(r':\s*$', subject):
        for line in lines[1:]:
            if line.strip():
                return f"{subject} {line.strip()}"
    return subject


def _has_releasable_commits(commits, parsers):
    """Returns True if at least one commit's subject matches a non-skip parser."""
    for commit in commits:
        subject = commit.splitlines()[0]
        for p in parsers:
            msg = p.get("message", "")
            if not msg:
                continue
            if re.match(msg, subject):
                if not p.get("skip", False):
                    return True
                break
    return False


def _print_cliff_rules(parsers, bump_cfg, toml_path=None):
    """Prints the raw cliff.toml content and commit line structure examples."""
    if toml_path:
        try:
            with open(toml_path) as f:
                lines = f.readlines()
            print(">>> cliff.toml commit_parsers:")
            inside = False
            for line in lines:
                if not inside and "commit_parsers" in line and "[" in line:
                    inside = True
                if inside:
                    print(f"    {line}", end="")
                    if line.rstrip().endswith("]"):
                        break
        except OSError:
            print(f">>> cliff.toml: (could not read {toml_path})")
    print("")

    if not parsers:
        print(">>> (no commit_parsers found — git-cliff will use its defaults)")
        return

    print(">>> How a commit line must look:")
    print("      type[location]: description")
    print("      type[location]!: description")
    print("")
    print("    Rules:")
    print("      - 'type' must start at the very beginning of the line — NO leading spaces")
    print("      - 'type' must be lowercase and must be one of the commit types defined in the")
    print("        cliff.toml commit_parsers shown above (e.g. feat, fix, chore, ...). Any other")
    print("        type is either skipped or treated as unreleasable, depending on your cliff.toml")
    print("      - '[location]' must follow the type immediately — no space between them")
    print("      - After ']' ONLY ':' or '!:' are valid — anything else (space, '!!', etc.)")
    print("        causes the line to be treated as continuation text of the previous commit")
    print("      - '!' forces a major bump regardless of type")
    print("      - Multiple locations: type[loc1, loc2]: description")
    print("      - Wildcards: type[*]: (one level), type[base/*]: (one level under base/),")
    print("        type[**]: (every VERSION.txt-bearing dir, any depth), type[base/**]: (same, under base/)")
    print("")
    print("    Examples based on your cliff.toml:")

    seen = set()
    for p in parsers:
        pattern = p.get("message", "")
        skip    = p.get("skip", False)
        tname   = pattern.lstrip("^").split("\\")[0].split("(")[0].rstrip()
        if tname and tname not in seen:
            seen.add(tname)
            suffix = "  (no release)" if skip else ""
            print(f"      {tname}[myservice]: your description here{suffix}")


def _print_location_commits(location_to_commits):
    """Prints commits grouped by location."""
    print("\033[1;4;33m>>> COMMITS TO PROCESS:\033[0m")
    print("\033[33m    Each [location] is the component whose VERSION.txt/CHANGELOG.md will be updated.\033[0m")
    print("\033[33m    The commits listed under it are the exact entries that will be written into that changelog.\033[0m")
    for loc in sorted(location_to_commits):
        display_loc = loc if loc else ""
        print(f"    [{display_loc}]")
        for commit in sorted(location_to_commits[loc]):
            print("      " + commit.replace("\n", "\n      "))


def _retrieve_pull_request_message():
    """pull_request event -> fetch the PR description from the Bitbucket Server API."""
    try:
        token      = os.environ["PLUGIN_BITBUCKET_TOKEN"]
        server_url = os.environ["CI_FORGE_URL"]
        repo_owner = os.environ["CI_REPO_OWNER"]
        repo_name  = os.environ["CI_REPO_NAME"]
        pr_number  = os.environ["CI_COMMIT_PULL_REQUEST"]
    except KeyError as e:
        print(f">>> ERROR: missing {e} — required to fetch the PR description from Bitbucket.")
        return None
    api_url = f"{server_url}/rest/api/1.0/projects/{repo_owner}/repos/{repo_name}/pull-requests/{pr_number}"
    print(f">>> [INFO] Fetching PR #{pr_number} from {api_url}")
    try:
        req = Request(api_url, headers={"Authorization": f"Bearer {token}"})
        import json
        pr = json.loads(urlopen(req).read())
    except Exception as e:
        print(f">>> ERROR: could not fetch PR #{pr_number} from Bitbucket: {e}")
        return None
    print(f">>> [INFO] PR #{pr.get('id')} — title: {pr.get('title', '')}")
    return pr.get("description", "")


def _retrieve_manual_message():
    """manual event -> use the PLUGIN_MESSAGE env var as-is."""
    message = os.getenv("PLUGIN_MESSAGE", "")
    if not message:
        print(">>> ERROR: PLUGIN_MESSAGE is empty — required for a manual run.")
        return None

    lines = message.splitlines()
    print("")
    print("\033[1;33m==================================================================\033[0m")
    print("\033[1;33m>>> PLUGIN_MESSAGE — this is the EXACT message YOU entered for this\033[0m")
    print("\033[1;33m>>> manual run. Every release below is parsed from these lines.\033[0m")
    print("\033[1;33m==================================================================\033[0m")
    print("\033[33m>>> [INFO] Source: PLUGIN_MESSAGE env var (used as-is, not stripped)\033[0m")
    print("\033[36m>>> ----------------------- BEGIN PLUGIN_MESSAGE -----------------------\033[0m")
    for n, line in enumerate(lines, 1):
        marked = line.replace("\t", "\\t")
        print(f"\033[36m>>> {n:>3} | {marked}\033[0m")
    print("\033[36m>>> ------------------------ END PLUGIN_MESSAGE ------------------------\033[0m")
    print("\033[1;33m>>> If nothing releases below, re-check the message above: a leading\033[0m")
    print("\033[1;33m>>> space, a wrong type, or a missing [location] makes a line IGNORED.\033[0m")
    print("\033[1;33m==================================================================\033[0m")
    print("")
    return message


def _retrieve_push_message():
    """Any other event -> assume a push carrying a PR merge commit, and extract
    the DESCRIPTION section from `git log -1 --pretty=%B`."""
    result = run_command("git log -1 --pretty=%B")
    message = result.stdout
    marker = "DESCRIPTION"
    if marker in message:
        return message.split(marker, 1)[1].strip()
    print(f">>> WARNING: no '{marker}' section found in commit message — using the full message as-is")
    return message.strip()


def _retrieve_message():
    """Determines the release message. Dispatches on CI_PIPELINE_EVENT."""
    event = os.getenv("CI_PIPELINE_EVENT", "manual")
    print(f">>> [INFO] Retrieving message directly (CI_PIPELINE_EVENT={event})")

    match event:
        case "pull_request":
            return _retrieve_pull_request_message()
        case "manual":
            return _retrieve_manual_message()
        case _:
            return _retrieve_push_message()


def _load_declared_images(root_path):
    """
    Returns the set of paths listed in <root_path>/images.txt (empty set if
    that file doesn't exist — e.g. a builtin repo). These are the CA-managed
    locations: version-file requires them to be reached only through a
    wildcard, never bumped by naming them directly (see
    _reject_direct_bumps_of_declared_images).
    """
    images_file = os.path.join(root_path, "images.txt")
    if not os.path.exists(images_file):
        return set()
    declared = set()
    with open(images_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            declared.add(line)
    return declared


def _scaffold_declared_images(root_path, declared):
    """
    Ensures every path in `declared` (from images.txt) has a folder with a
    VERSION.txt (created empty if missing). An empty VERSION.txt just means
    "declared but not yet built" — the main release loop seeds a new
    component's starting version the same way regardless of how it came to
    exist (images.txt line or a first-ever commit touching it directly), so
    there's exactly one place that decides a starting version, not two.

    A no-op for any repo without an images.txt at its root (e.g. builtin repos).
    """
    for line in sorted(declared):
        full_path = os.path.normpath(os.path.join(root_path, line))
        os.makedirs(full_path, exist_ok=True)
        version_file = os.path.join(full_path, "VERSION.txt")
        if not os.path.exists(version_file):
            open(version_file, "w").close()
            print(f">>> [INFO] scaffolded declared image from images.txt: {line}")


def _reject_direct_bumps_of_declared_images(location_to_commits, declared):
    """
    A location declared in images.txt is CA-managed: its version is only ever
    supposed to move because the CA moved, reached through a wildcard
    ([**]/[prefix/**]) alongside a root bump — never because someone named it
    directly in a commit line. Naming one directly would silently bump that
    one image out of step with the CA and every other hardened image, so this
    fails the whole run instead — loud and before anything is written,
    rather than a quiet, hard-to-notice drift.

    Only literal (non-wildcard) keys in location_to_commits are checked — a
    location reached purely via [**] never appears here as a literal key, so
    the legitimate rotation path is untouched. A no-op if images.txt doesn't
    exist (declared is empty) — never fires in a builtin repo.
    """
    if not declared:
        return
    violations = sorted(
        loc for loc in location_to_commits
        if "*" not in loc and loc in declared
    )
    if violations:
        print(">>> ERROR: the following location(s) are declared in images.txt (CA-managed) and")
        print("           were named directly in the commit message — not allowed:")
        for loc in violations:
            print(f"             {loc}")
        print("           A CA-managed image's version can only move because the CA moved.")
        print("           Target it via a wildcard instead, together with a root bump:")
        print("             feat[]: rotate CA")
        print("             feat[**]: rotate CA")
        sys.exit(1)


def _derive_from_image(location, mirror_registry):
    """<source>/<org>/<image>/<tag> -> <mirror_registry>/<source>/<org>/<image>:<tag>.
    Returns None if the location doesn't look like that shape, or its first
    segment isn't a known source alias."""
    if "/" not in location:
        return None
    rest, tag = location.rsplit("/", 1)
    source_alias = rest.split("/", 1)[0]
    if source_alias not in _KNOWN_SOURCE_ALIASES:
        return None
    return f"{mirror_registry}/{rest}:{tag}"


def _generate_dockerfile_if_missing(full_path, location, root_path, mirror_registry):
    """
    Generates <full_path>/Dockerfile from <root_path>/Dockerfile.template when:
      - the location has no Dockerfile yet, or
      - its existing Dockerfile starts with the "# GENERATED by" marker (so a
        template edit propagates to every already-built image on its next bump,
        instead of going stale silently).
    A hand-written Dockerfile (no marker) is the escape hatch and is left alone.
    A no-op if <root_path>/Dockerfile.template doesn't exist (e.g. builtin repos,
    which always ship a real per-image Dockerfile already).
    """
    template_path = os.path.join(root_path, "Dockerfile.template")
    if not os.path.exists(template_path):
        return

    dockerfile_path = os.path.join(full_path, "Dockerfile")
    if os.path.exists(dockerfile_path):
        with open(dockerfile_path) as f:
            first_line = f.readline()
        if not first_line.startswith("# GENERATED by"):
            print(f">>> [INFO] {location}: using hand-written Dockerfile (escape hatch)")
            return

    if not mirror_registry:
        print(f">>> WARNING: {location} needs a generated Dockerfile but PLUGIN_MIRROR_REGISTRY is not set — skipping.")
        return

    from_image = _derive_from_image(location, mirror_registry)
    if not from_image:
        print(f">>> WARNING: {location} does not look like <source>/<org>/<image>/<tag> — cannot generate a Dockerfile.")
        return

    with open(template_path) as f:
        template = f.read()
    with open(dockerfile_path, "w") as f:
        f.write(template.replace("{{FROM_IMAGE}}", from_image))
    print(f">>> [INFO] {location}: generated Dockerfile (FROM {from_image})")


def _update_root_changelog(root_path, location, version_prefix, new_version, note):
    """
    Appends one bullet under <location>'s own "## <location>" section in the
    repo-root CHANGELOG.md, creating that section if it doesn't exist yet.
    The newest bullet always lands right under the heading, so each location's
    section reads newest-first — a component that only ever started at v2.0.0
    simply has no v1.0.0 line, because none was ever inserted for it.
    """
    changelog_path = os.path.join(root_path, "CHANGELOG.md")
    heading = f"## {location}"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    line = f"- **{version_prefix}{new_version}** — {date} — {note}"

    if not os.path.exists(changelog_path):
        with open(changelog_path, "w") as f:
            f.write("# Changelog\n")

    with open(changelog_path) as f:
        content = f.read()

    lines = content.splitlines()
    if heading in lines:
        idx = lines.index(heading)
        lines.insert(idx + 1, line)
        new_content = "\n".join(lines) + "\n"
    else:
        new_content = content.rstrip("\n") + f"\n\n{heading}\n{line}\n"

    with open(changelog_path, "w") as f:
        f.write(new_content)


def release():
    # ── Validate required env vars ────────────────────────────────────────────
    missing = []
    if not os.getenv("PLUGIN_CHANGELOG_LEVEL"):
        missing.append(
            "  PLUGIN_CHANGELOG_LEVEL — integer depth of component locations in your PR body.\n"
            "    Level 0 -> root only: feat[][...]   Level 1 -> top-level: feat[nati][...]\n"
            "    Level 2 -> nested:    feat[plugins/docker][...]   Example: PLUGIN_CHANGELOG_LEVEL=1\n"
            "    May be a comma-separated list to allow several depths at once, e.g. PLUGIN_CHANGELOG_LEVEL=0,4"
        )
    if not os.getenv("PLUGIN_BASE_PATH"):
        missing.append(
            "  PLUGIN_BASE_PATH — root directory; all [location] paths are resolved relative to this.\n"
            "    Example: PLUGIN_BASE_PATH=."
        )
    if missing:
        print(">>> ERROR: Missing required environment variables:\n")
        for m in missing:
            print(m)
        sys.exit(1)

    try:
        changelog_levels = _normalize_changelog_levels(os.getenv("PLUGIN_CHANGELOG_LEVEL"))
    except (TypeError, ValueError):
        print(">>> ERROR: PLUGIN_CHANGELOG_LEVEL must be a non-negative integer, or a "
              "comma-separated list of them (e.g. 1 or 2,3,4).")
        return

    pr_body = _retrieve_message()
    if pr_body is None:
        sys.exit(1)
    with open("pr_body.txt", "w") as _f:
        _f.write(pr_body)

    root_path              = os.getenv("PLUGIN_BASE_PATH")
    output_tags_file       = os.getenv("PLUGIN_OUTPUT_TAGS_FILE", "")
    output_locations_file  = os.getenv("PLUGIN_OUTPUT_LOCATIONS_FILE", "")
    if output_tags_file:
        open(output_tags_file, "w").close()
    if output_locations_file:
        open(output_locations_file, "w").close()
    exclude_regex = os.getenv("PLUGIN_SCOPE_EXCLUDE_REGEX", "")
    try:
        verbose = int(os.getenv("PLUGIN_VERBOSE", "0"))
    except ValueError:
        verbose = 0
    initial_tag_version = os.getenv("PLUGIN_INITIAL_TAG", "1.0.0").lstrip("v")
    version_prefix       = "v" if os.getenv("PLUGIN_V_PREFIX", "true").lower() == "true" else ""
    mirror_registry       = os.getenv("PLUGIN_MIRROR_REGISTRY", "")

    _bundled_toml = os.path.join(os.path.dirname(__file__), "cliff.toml")
    global_toml   = os.getenv("PLUGIN_CLIFF_TOML") or ("./cliff.toml" if os.path.exists("./cliff.toml") else _bundled_toml)
    _cliff_verbose = " -vv" if verbose >= 2 else (" -v" if verbose == 1 else "")
    cliff_cmd_base = f"git cliff --config {global_toml}{_cliff_verbose}"

    if not os.path.exists(global_toml):
        print(f">>> ERROR: cliff.toml not found at {global_toml}")
        return

    parsers, bump_cfg = load_cliff_parsers(global_toml)
    _print_cliff_rules(parsers, bump_cfg, global_toml)

    if changelog_levels:
        print(f">>> PLUGIN_CHANGELOG_LEVEL={','.join(str(l) for l in sorted(changelog_levels))}")
    print(f">>> PLUGIN_BASE_PATH='{root_path}' — root directory; all [location] paths are resolved relative to this")

    # ── Scaffold any brand-new locations declared in images.txt (no-op if absent) ──
    declared_images = _load_declared_images(root_path)
    _scaffold_declared_images(root_path, declared_images)

    # ── Parse PR body ─────────────────────────────────────────────────────────
    location_to_commits = parse_pr_body(pr_body, parsers, changelog_level=changelog_levels)

    if not location_to_commits:
        print(">>> No release commits detected in PR Body.")
        return

    _print_location_commits(location_to_commits)

    # ── Refuse to bump a CA-managed image by naming it directly ────────────────
    _reject_direct_bumps_of_declared_images(location_to_commits, declared_images)

    # ── Expand wildcards + apply exclusions ───────────────────────────────────
    had_wildcards = any("*" in loc for loc in location_to_commits)
    location_to_commits = _expand_locations(
        location_to_commits, root_path, exclude_regex,
        include_root_in_double_star=bool(declared_images),
    )

    if not location_to_commits:
        print(">>> No components to release after expansion/filtering.")
        return

    if output_locations_file:
        with open(output_locations_file, "w") as f:
            for loc in sorted(location_to_commits):
                f.write(f"{loc}\n")
        print(f">>> [INFO] Locations written to '{output_locations_file}': {sorted(location_to_commits)}")

    if had_wildcards:
        print("\033[1;4;33m>>> COMMITS AFTER WILDCARD EXPANSION:\033[0m")
        print("\033[33m    Wildcards replaced with concrete component paths.\033[0m")
        for loc in sorted(location_to_commits):
            display_loc = loc if loc else ""
            print(f"    [{display_loc}]")
            for commit in sorted(location_to_commits[loc]):
                print("      " + commit.replace("\n", "\n      "))

    # ── Process every location: no git tag/branch resolution at all — the
    # current version is read straight off VERSION.txt in the working tree.
    # A local, ephemeral tag is created only to give git-cliff's --bump
    # something to compute the next version from, and is deleted immediately
    # after; nothing here fetches, checks out, or depends on git ancestry.
    created_tags = []
    for location in sorted(location_to_commits):
        commits = location_to_commits[location]
        is_root = (location == "")

        vp = re.escape(version_prefix)
        if is_root:
            tag_prefix             = version_prefix
            cliff_tag_prefix       = version_prefix
            component_tag_pattern  = f"^{vp}[0-9]+\\.[0-9]+\\.[0-9]+$"
            full_path              = os.path.normpath(root_path)
        else:
            path_slug  = location.replace("/", "-").replace("\\", "-")
            tag_prefix = f"{path_slug}-{version_prefix}"
            # git-cliff's own version detection scans the WHOLE tag string for
            # something semver-shaped — a location like a hardened image whose
            # path includes a dotted upstream tag ("plugin-git/2.9.3") embeds
            # an earlier X.Y.Z-looking substring in the slug, which git-cliff
            # can mistake for part of the version it's tracking, corrupting the
            # bump (verified: "...-2.9.3-v1.0.0" bumped "feat" to a patch
            # v1.0.1 instead of the correct minor v1.1.0; "...-9.4-v1.0.0",
            # with no full X.Y.Z-shaped substring, bumped correctly). Every
            # git-cliff-facing name (--tag-pattern, the ephemeral bump tag, the
            # --tag used for changelog generation) uses this sanitized slug
            # instead — dots in the LOCATION replaced with underscores so only
            # the real "-v1.2.3" suffix looks like a version to git-cliff.
            # tag_prefix (real dots) is still what's written to the output
            # tags file / VERSION.txt-derived display strings.
            cliff_slug             = path_slug.replace(".", "_")
            cliff_tag_prefix       = f"{cliff_slug}-{version_prefix}"
            component_tag_pattern  = f"^{cliff_slug}-{vp}[0-9]+\\.[0-9]+\\.[0-9]+$"
            full_path              = os.path.normpath(os.path.join(root_path, location))

        display_name = location if location else "(root)"
        print("")
        print(f"\033[1;31m--- Processing: {display_name} ---\033[0m")
        print("")

        os.makedirs(full_path, exist_ok=True)
        version_file      = os.path.join(full_path, "VERSION.txt")
        root_version_file = os.path.join(root_path, "VERSION.txt")

        current = ""
        if os.path.exists(version_file):
            current = open(version_file).read().strip()

        # Three distinct cases — kept separate on purpose. Conflating "seeded"
        # with "existing" would bump a brand-new location one step PAST the
        # value it just adopted (e.g. seed v2.0.0, then bump to v2.1.0) instead
        # of landing exactly on it, defeating the whole point of seeding.
        seed_version = None
        if not current and not is_root and os.path.exists(root_version_file):
            seed_version = open(root_version_file).read().strip() or None

        all_commits = sorted(commits)
        with_commit_args  = " ".join(f"--with-commit {shlex.quote(c)}" for c in all_commits)
        bump_commit_args  = " ".join(f"--with-commit {shlex.quote(_bump_subject(c))}" for c in all_commits)

        # CA-managed: root (whenever this repo has an images.txt at all) and
        # every images.txt-declared image. A repo either is images.txt-style
        # (root IS the CA version, every rotation is inherently a major event)
        # or it isn't — this is a data-driven signal (images.txt existing),
        # not a hardcoded "this is cicd-images" special case.
        is_ca_managed = bool(declared_images) and (is_root or location in declared_images)

        if current:
            print(f">>> [INFO] Current version: {current}")
            if is_ca_managed:
                # CA-managed locations always bump MAJOR, regardless of the
                # commit type used — the commit type/message wording still has
                # to be a recognized, non-skip type (so a stray unrecognized
                # or skip=true-only line can't trigger anything), but it no
                # longer decides the bump LEVEL. This is also what keeps every
                # hardened image locked to root's version: if the level were
                # still type-driven, a plain "feat" root line and a "feat[**]"
                # image line would both be minor bumps individually, but
                # starting from possibly-different current versions they
                # wouldn't necessarily land on the same number — forcing
                # major for both removes any ambiguity about that.
                if not _has_releasable_commits(all_commits, parsers):
                    print(f">>> SKIP: no releasable commits for {display_name} (all commits are skip=true).")
                    continue
                major = int(current.split(".")[0])
                new_version = f"{major + 1}.0.0"
                print(f">>> [INFO] CA-managed — forced MAJOR bump: {new_version}")
            else:
                # Ordinary component: bump forward from its own current
                # version using git-cliff's usual type-driven rules.
                ephemeral_tag = f"{cliff_tag_prefix}{current}"
                run_command(f"git tag -f {shlex.quote(ephemeral_tag)} HEAD")

                bump_cmd = " ".join(filter(None, [
                    cliff_cmd_base,
                    f"--tag-pattern '{component_tag_pattern}'",
                    "--bump --bumped-version",
                    bump_commit_args,
                    "-- HEAD..HEAD",
                ]))
                print(f">>> [VERBOSE] bump_cmd: {bump_cmd}")
                bumped = run_command(bump_cmd)
                run_command(f"git tag -d {shlex.quote(ephemeral_tag)}")

                print(f">>> [VERBOSE] bump stdout: {bumped.stdout.strip()}")
                if bumped.stderr.strip():
                    print(">>> [VERBOSE] bump stderr:")
                    for line in bumped.stderr.strip().splitlines():
                        print(f"    {line}")

                bumped_cliff_tag = bumped.stdout.strip()
                if not bumped_cliff_tag:
                    print(f">>> SKIP: no releasable commits for {display_name}")
                    continue
                if bumped_cliff_tag == ephemeral_tag:
                    print(f">>> SKIP: bumped version equals current ({current}) — no releasable commits for {display_name}")
                    continue

                new_version = bumped_cliff_tag[len(cliff_tag_prefix):]
                print(f">>> [INFO] Calculated new version: {new_version}")
        elif seed_version:
            # Brand-new location: adopt the repo's current root VERSION.txt
            # directly — no bump computed on top of it. A hardened image added
            # while the CA is already at v2.0.0 starts AT v2.0.0, not v2.1.0.
            if not _has_releasable_commits(all_commits, parsers):
                print(f">>> SKIP: no releasable commits for {display_name} (all commits are skip=true).")
                continue
            new_version = seed_version
            print(f">>> [INFO] No prior version — seeded from root VERSION.txt: {new_version}")
        else:
            # True first release: no prior version, no root VERSION.txt to seed from.
            if not _has_releasable_commits(all_commits, parsers):
                print(f">>> SKIP: no releasable commits for {display_name} (all commits are skip=true).")
                continue
            new_version = initial_tag_version
            print(f">>> [INFO] No current or seed version — first release: {new_version}")

        # ── Dockerfile: generate/regenerate from template if applicable (no-op
        # unless PLUGIN_BASE_PATH/Dockerfile.template exists) ─────────────────
        _generate_dockerfile_if_missing(full_path, location, root_path, mirror_registry)

        # ── Write this location's own CHANGELOG.md via git-cliff ──────────────
        changelog_path = os.path.join(full_path, "CHANGELOG.md")
        if os.path.exists(changelog_path):
            output_flag = f"--prepend {changelog_path}"
            print(f">>> [INFO] {display_name}: CHANGELOG exists — using --prepend")
        else:
            output_flag = f"--output {changelog_path}"
            print(f">>> [INFO] {display_name}: CHANGELOG not found — using --output")

        new_full_tag = f"{tag_prefix}{new_version}"
        cliff_cmd = " ".join(filter(None, [
            cliff_cmd_base,
            f"--tag-pattern '{component_tag_pattern}'",
            f"--tag '{new_full_tag}'",
            with_commit_args,
            output_flag,
            "-- HEAD..HEAD",
        ]))
        print(f">>> [VERBOSE] cliff_cmd: {cliff_cmd}")
        res = run_command(cliff_cmd)
        print(f">>> [VERBOSE] cliff stdout: {res.stdout.strip()}")
        if res.stderr.strip():
            print(">>> [VERBOSE] cliff stderr:")
            for line in res.stderr.strip().splitlines():
                print(f"    {line}")

        if res.returncode != 0:
            print(f">>> ERROR generating changelog for {display_name}: {res.stderr.strip()}")
            continue

        # ── Write VERSION.txt + the repo-root changelog index ─────────────────
        with open(version_file, "w") as f:
            f.write(new_version)

        note = "first release" if not current else "bumped"
        _update_root_changelog(root_path, display_name, version_prefix, new_version, note)

        created_tags.append(new_full_tag)
        if output_tags_file:
            with open(output_tags_file, "a") as f:
                f.write(f"{new_full_tag}\n")
            print(f">>> [INFO] Tag '{new_full_tag}' written to '{output_tags_file}'")

    if created_tags:
        print("")
        print("\033[1;34m>>> Tags to be created by pipeline:\033[0m")
        for tag in created_tags:
            print(f"\033[1;34m    {tag}\033[0m")
    else:
        print("\033[1;34m>>> No new tags created.\033[0m")


if __name__ == "__main__":
    release()
