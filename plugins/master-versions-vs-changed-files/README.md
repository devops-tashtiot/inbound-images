# master-versions-vs-changed-files

Woodpecker CI plugin that compares the locations declared in a PR description against the directories that actually changed on disk, reporting gaps in both directions.

Designed to run between the **changed-files** and **master-versions** steps so you catch mistakes before they silently slip through.

---

## What it reports

| Situation | Meaning |
|-----------|---------|
| **MISSING from PR description** | Directory changed on disk but not mentioned in the PR body — master-versions would silently skip it, leaving it unreleased |
| **Extra in PR description** | Location mentioned in the PR body but no files changed there — likely a typo or stale entry |

---

## How it works

1. Reads the locations file produced by master-versions (`PLUGIN_OUTPUT_LOCATIONS_FILE`) — one expanded, filtered location per line
2. Reads the changed directories file produced by changed-files (`PLUGIN_OUTPUT_FILE`) — one directory per line
3. Compares the two sets
4. Reports missing entries in both directions
5. Exits with code `1` (failing the pipeline) or `0` (warning only) based on `PLUGIN_FAIL_ON_MISMATCH`

---

## Variables

### Required

| Variable | Description |
|----------|-------------|
| `PLUGIN_MASTER_VERSIONS_LOCATIONS_FILE` | Path to the locations file written by master-versions via `PLUGIN_OUTPUT_LOCATIONS_FILE` |
| `PLUGIN_CHANGED_DIRS_FILE` | Path to the changed directories file written by the changed-files plugin |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `PLUGIN_FAIL_ON_MISMATCH` | `false` | `true` → fail the pipeline on mismatch (exit 1). `false` → warn and continue. |

---

## Pipeline integration

```yaml
steps:
  - name: get-changed-dirs
    image: netanelzucaim123/changed-files
    settings:
      output_file: changed_dirs.txt
      output_type: dirs
      folder_depth: 2
      dedup: "true"

  - name: release
    image: netanelzucaim123/master-versions
    settings:
      message_file: pr_body.txt
      base_path: .
      changelog_level: 2
      output_tags_file: new_tags.txt
      output_locations_file: pr_locations.txt   # ← enables the locations file

  - name: diff-check
    image: netanelzucaim123/master-versions-vs-changed-files
    settings:
      locations_file: pr_locations.txt
      changed_dirs_file: changed_dirs.txt
      fail_on_mismatch: "true"    # fail the pipeline if there is a mismatch
```

---

## Example output

### All good
```
>>> Locations from PR  (2 entries):
    plugins/docker
    plugins/nati
>>> Changed dirs       (2 entries):
    plugins/docker
    plugins/nati

>>> OK: every changed directory is covered in the PR description.
```

### Missing from PR
```
>>> MISSING from PR description (changed on disk but not released):
    plugins/nati

>>> WARNING: diff found — pipeline continues (PLUGIN_FAIL_ON_MISMATCH=warning)
```

### Extra in PR
```
>>> Extra in PR description (mentioned but no files changed):
    plugins/docker

>>> ERROR: diff found — failing pipeline (PLUGIN_FAIL_ON_MISMATCH=error)
```
