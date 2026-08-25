# kaniko-master-versions

Woodpecker CI plugin that reads the tags produced by the **MasterVersions plugin** (semantic versioning via git-cliff + message file), resolves each tag to a `Dockerfile` on disk, and builds + pushes the Docker image via Kaniko.

Designed to run as the final step of the release pipeline, right after the MasterVersions plugin creates the git tags.

---

## How it works

1. Reads a list of tags from the file written by the MasterVersions plugin (e.g. `new_tags.txt`)
2. For each tag, extracts the **slug** and **version**
3. Scans `PLUGIN_BASE_PATH` to find the `Dockerfile` whose parent directory matches the slug
4. Builds and pushes the image via `/kaniko/executor`

### Tag → path resolution

The plugin splits every tag into a **slug** (everything before the version) and a **version** (the trailing semver).
The slug is then converted back to a filesystem path by replacing `-` with `/` and scanning `PLUGIN_BASE_PATH` for a matching directory.

| Tag | PLUGIN_BASE_PATH | Resolved path | Dockerfile location | Image pushed |
|-----|-------------|---------------|---------------------|--------------|
| `harel-v1.3.4` | `check/plugins` | `harel` | `check/plugins/harel/Dockerfile` | `registry/repo/harel:v1.3.4` |
| `netanel-1.0.0` | `check/plugins` | `netanel` | `check/plugins/netanel/Dockerfile` | `registry/repo/netanel:1.0.0` |
| `check-plugins-harel-v1.3.4` | `.` | `check/plugins/harel` | `check/plugins/harel/Dockerfile` | `registry/repo/check/plugins/harel:v1.3.4` |
| `v1.5.6` | `check/plugins/harel` | `.` (root) | `check/plugins/harel/Dockerfile` | `registry/repo:v1.5.6` |

### How the Dockerfile path is built

The full path to the Dockerfile is always: `PLUGIN_BASE_PATH / resolved_path / Dockerfile`

```
Tag                        Slug               PLUGIN_BASE_PATH        Resolved path     Dockerfile
─────────────────────────────────────────────────────────────────────────────────────────────
harel-v1.3.4               harel              check/plugins   →  harel          →  check/plugins/harel/Dockerfile
netanel-1.0.0              netanel            check/plugins   →  netanel        →  check/plugins/netanel/Dockerfile
check-plugins-harel-v1.3.4 check-plugins-harel  .            →  check/plugins/harel → check/plugins/harel/Dockerfile
plugins-docker-2.0.0       plugins-docker     .               →  plugins/docker →  plugins/docker/Dockerfile
base-argo-v1.1.0           base-argo          services        →  base/argo      →  services/base/argo/Dockerfile
v1.5.6                     (empty)            check/plugins/harel → . (root)    →  check/plugins/harel/Dockerfile
```

The slug-to-path conversion scans `PLUGIN_BASE_PATH` recursively for a `Dockerfile` whose parent directory,
when its slashes are replaced with hyphens, matches the slug exactly.
This means `PLUGIN_BASE_PATH` controls the tag prefix length — a deeper base means shorter slugs and shorter tags.

> **Note — slug collision:** Because `/` and `-` both map to `-`, a path like `plugins/kaniko-master-versions/`
> and `plugins-kaniko/master-versions/` would produce the same slug `plugins-kaniko-master-versions`.
> This is extremely unlikely in practice — group directories (`plugins/`, `base/`, `apps/`) are short words
> with no hyphens, so no real ambiguity exists. If it ever becomes a problem, the fix is a one-line change
> in `release.py`: replace `/` with `_` instead of `-` when building slugs, making paths unambiguous.

### Supported tag formats

All of the following are understood — no fixed format is assumed:

```
harel-v1.3.4          slug=harel           version=v1.3.4
plugins-netanel-1.0.0 slug=plugins-netanel  version=1.0.0
netanel-1.8           slug=netanel          version=1.8
1.5.6                 slug=(empty)          version=1.5.6  → Dockerfile at PLUGIN_BASE_PATH root
```

---

## Variables

### Required

| Variable | Description |
|----------|-------------|
| `PLUGIN_BASE_PATH` | Directory to scan for Dockerfiles. Set to wherever your component folders live. |
| `PLUGIN_USERNAME` | Registry username |
| `PLUGIN_PASSWORD` | Registry password |
| `PLUGIN_TAGS_FILE` **or** `PLUGIN_TAGS` | Tags to process. File path (newline-separated) or inline comma/newline-separated string. |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `PLUGIN_REGISTRY` | `index.docker.io` | Docker registry |
| `PLUGIN_REPO` | `""` | Image repository/namespace prefix |
| `PLUGIN_DOCKERFILE` | `Dockerfile` | Dockerfile filename to look for |
| `PLUGIN_ALIASES` | *(not set)* | Comma-separated alias tags pushed alongside the version tag. Not set by default — only the exact version tag is pushed. |
| `PLUGIN_DRY_RUN` | `false` | Set to `"true"` to skip the actual push (`--no-push`) |
| `PLUGIN_LOG_LEVEL` | `info` | **Kaniko executor log verbosity.** Passed directly as the `-v` flag to `/kaniko/executor`. Controls how much output kaniko itself produces — this is not the plugin's own log level. Available values: `panic`, `fatal`, `error`, `warn`, `info`, `debug`, `trace`. |
| `PLUGIN_SKIP_TLS_VERIFY` | `false` | Set to `"true"` to add `--skip-tls-verify` |
| `PLUGIN_INSECURE` | `false` | Set to `"true"` to add `--insecure` |

### PLUGIN_ALIASES examples

```yaml
# Push version tag only (default — no aliases)
# → registry/repo/harel:v1.3.4

# Push version + latest
PLUGIN_ALIASES: "latest"
# → registry/repo/harel:v1.3.4
# → registry/repo/harel:latest

# Push version + prod + staging
PLUGIN_ALIASES: "prod,staging"
# → registry/repo/harel:v1.3.4
# → registry/repo/harel:prod
# → registry/repo/harel:staging
```

---

## Pipeline integration

The two plugins are designed to run as consecutive steps in the same pipeline:

1. **master-versions** calculates versions, writes changelogs, and appends every
   created tag (one per line) to the file set in `PLUGIN_OUTPUT_TAGS_FILE` (e.g. `new_tags.txt`).

2. **kaniko-master-versions** reads that same file via `PLUGIN_TAGS_FILE`, resolves each tag to a
   Dockerfile on disk, and builds + pushes the corresponding Docker image.

The file is the contract between the two steps — master-versions writes it, kaniko-master-versions
reads it. Both steps must share the same workspace so the file is visible to both.

```
master-versions                       kaniko-master-versions
──────────────────────────────        ──────────────────────────────────────
run conventional commit script        reads new_tags.txt line by line
  → nati-v1.1.0                  ──►  nati-v1.1.0  → slug=nati  → PLUGIN_BASE_PATH/nati/Dockerfile
  → plugins-docker-v2.0.0        ──►  plugins-docker-v2.0.0 → slug=plugins-docker → PLUGIN_BASE_PATH/plugins/docker/Dockerfile
appended to new_tags.txt              builds and pushes each image via Kaniko
```

If `new_tags.txt` is empty (no components released), kaniko-master-versions exits immediately with no builds.






