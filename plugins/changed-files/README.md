# changed-files

Woodpecker CI plugin that reads the list of files changed in the current pipeline, applies optional include/exclude filters, and writes the result to a file — as either file paths or directory paths.

---

## How it works

1. Reads changed files from the `CI_PIPELINE_FILES` environment variable (set automatically by Woodpecker CI)
2. Applies optional include/exclude regex filters
3. Converts to directory paths if `PLUGIN_OUTPUT_TYPE=dirs`, with optional depth truncation
4. Deduplicates the result (configurable)
5. Writes the output to `PLUGIN_OUTPUT_FILE`

---

## Variables

### Required

None — all variables have defaults. `CI_PIPELINE_FILES` is provided automatically by Woodpecker CI.

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `PLUGIN_OUTPUT_FILE` | `changed.txt` | Path to write the output to. The parent directory must already exist. |
| `PLUGIN_OUTPUT_TYPE` | `files` | What to output. `files` — one file path per line. `dirs` — the parent directory of each changed file. |
| `PLUGIN_FOLDER_DEPTH` | `0` | Only used when `PLUGIN_OUTPUT_TYPE=dirs`. Truncates each directory path to this many levels deep. `0` means full path. |
| `PLUGIN_DEDUP` | `true` | `true` — remove duplicate lines from the output. `false` — keep all lines as-is. |
| `PLUGIN_BASE_PATH` | *(not set)* | Strip this path prefix from every changed file before any further processing. Files not under this path are silently dropped. `.` and unset are a no-op. |
| `PLUGIN_EXCLUDE_FILES_REGEX` | *(not set)* | Extended regex applied to the (already stripped) path. Any file whose path matches is removed from the output. |
| `PLUGIN_INCLUDE_FILES_REGEX` | *(not set)* | Extended regex applied to the (already stripped) path. Only files whose path matches are kept. Applied after exclude. |

---

## Examples

### Output changed file paths (default)

```yaml
PLUGIN_OUTPUT_FILE: "changed.txt"
PLUGIN_OUTPUT_TYPE: "files"
```

Given changed files `plugins/docker/main.go`, `plugins/docker/Dockerfile`, `docs/readme.md`:

```
plugins/docker/main.go
plugins/docker/Dockerfile
docs/readme.md
```

---

### Output unique changed directories

```yaml
PLUGIN_OUTPUT_TYPE: "dirs"
PLUGIN_DEDUP: "true"
```

```
plugins/docker
docs
```

---

### Output top-level directories only (depth=1)

```yaml
PLUGIN_OUTPUT_TYPE: "dirs"
PLUGIN_FOLDER_DEPTH: "1"
PLUGIN_DEDUP: "true"
```

```
plugins
docs
```

---

### Strip a base path prefix

```yaml
PLUGIN_BASE_PATH: "nati/check"
PLUGIN_OUTPUT_TYPE: "dirs"
PLUGIN_DEDUP: "true"
```

Changed files: `nati/check/root/sagi/main.go`, `nati/check/core/handler.go`, `docs/readme.md`

```
root/sagi
core
```

`docs/readme.md` is dropped (not under `nati/check/`). All paths are relative to the base.

---

### Filter to only plugin changes

```yaml
PLUGIN_INCLUDE_FILES_REGEX: "^plugins/"
PLUGIN_OUTPUT_TYPE: "dirs"
PLUGIN_FOLDER_DEPTH: "2"
PLUGIN_DEDUP: "true"
```

Changed files: `plugins/docker/main.go`, `plugins/auth/handler.go`, `docs/readme.md`

```
plugins/docker
plugins/auth
```

---

### Exclude docs and test files

```yaml
PLUGIN_EXCLUDE_FILES_REGEX: "^docs/|_test\.go$"
```

---

## Pipeline integration

```yaml
steps:
  - name: get-changed-files
    image: netanelzucaim123/changed-files
    settings:
      output_file: changed.txt
      output_type: dirs
      folder_depth: 2
      dedup: true
      exclude_files_regex: "^docs/"

  - name: release
    image: netanelzucaim123/master-versions
    settings:
      # use changed.txt produced by the previous step
```

---

## Filter order

Filters are applied in this order:

1. **Base path strip** (`PLUGIN_BASE_PATH`) — prefix removed; files outside the base are dropped
2. **Exclude** (`PLUGIN_EXCLUDE_FILES_REGEX`) — matching files are dropped (runs on stripped path)
3. **Include** (`PLUGIN_INCLUDE_FILES_REGEX`) — only matching files are kept (runs on stripped path)
4. **Dedup** (`PLUGIN_DEDUP`) — duplicates are removed after all filtering
