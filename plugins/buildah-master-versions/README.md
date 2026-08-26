# buildah-master-versions

Woodpecker CI plugin that reads the tags produced by the **MasterVersions plugin** (semantic versioning via git-cliff + message file), resolves each tag to a `Dockerfile` on disk, and builds + pushes the Docker image via buildah.

Designed to run as the final step of the release pipeline, right after the MasterVersions plugin creates the git tags.

### TLS to Harbor

The image (`quay.io/buildah/stable`, Fedora-based) bakes in Cloudflare's Origin CA root
(`cloudflare-origin-ca-rsa-root.pem` → `/etc/pki/ca-trust/source/anchors/`, then `update-ca-trust`).
`harbor.devopstashtiot.page` is reached internally via ingress-nginx, which presents an Origin
CA-issued cert that no public trust store carries. Baking the root in lets `buildah push` verify
TLS normally, so the pipeline does **not** set `PLUGIN_SKIP_TLS_VERIFY` — see the `.woodpecker/`
build step. `PLUGIN_SKIP_TLS_VERIFY` / `PLUGIN_INSECURE` still exist as an escape hatch for other
registries (below).

---

## How it works

1. Reads a list of tags from the file written by the MasterVersions plugin (e.g. `new_tags.txt`)
2. For each tag, extracts the **slug** and **version**
3. Scans `PLUGIN_BASE_PATH` to find the `Dockerfile` whose parent directory matches the slug
4. Builds and pushes the image via `buildah bud` / `buildah push`

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

> **Note — slug collision:** Because `/` and `-` both map to `-`, a path like `plugins/buildah-master-versions/`
> and `plugins-kaniko/master-versions/` would produce the same slug `plugins-buildah-master-versions`.
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
| `PLUGIN_TAG_SUFFIX` | *(not set)* | String appended verbatim to the version before the image is built and pushed. e.g. `-abc123` → `:v1.2.0-abc123`, `_rc1` → `:v1.2.0_rc1`. |
| `PLUGIN_DRY_RUN` | `false` | Set to `"true"` to build the image but skip the push (`buildah bud` runs, `buildah push` is skipped) |
| `PLUGIN_LOG_LEVEL` | `info` | **buildah log verbosity.** Passed directly as `--log-level` to `buildah bud`. Available values: `panic`, `fatal`, `error`, `warn`, `info`, `debug`, `trace`. |
| `PLUGIN_SKIP_TLS_VERIFY` | `false` | Set to `"true"` to use `--tls-verify=false` on push/login |
| `PLUGIN_INSECURE` | `false` | Set to `"true"` to use `--tls-verify=false` on push/login |
| `PLUGIN_EXTRA_BUILD_CONTEXTS` | *(not set)* | Comma-separated `name=path` pairs (`path` relative to `PLUGIN_BASE_PATH`, same convention as a component's own `rel_path`). Passed to `buildah bud` as additional named `--build-context` entries, so a Dockerfile can `COPY --from=<name> <file> <dest>` from a folder outside its own component directory — most commonly a repo-root folder shared by every component (a CA cert, an injector script) — without needing a copy of that file duplicated into each component's own folder. |

### PLUGIN_EXTRA_BUILD_CONTEXTS example

Each component's real build context is still just its own folder — `buildah-master-versions`
always builds `PLUGIN_BASE_PATH/<component>` as the context, never the repo root (see "Tag → path
resolution" above) — so a plain `COPY certs/foo.pem ...` from a component's Dockerfile can never
reach a repo-root `certs/` folder; Docker/buildah refuse to `COPY` anything from outside the build
context. `PLUGIN_EXTRA_BUILD_CONTEXTS` adds one or more *named* contexts alongside the real one, so
`COPY --from=<name>` can reach them instead:

```yaml
environment:
  PLUGIN_BASE_PATH: .
  PLUGIN_EXTRA_BUILD_CONTEXTS: "cacerts=certs,cascripts=scripts"
```

```dockerfile
# plugins/foo/Dockerfile — context is plugins/foo/, but --from=cacerts/--from=cascripts
# reach the repo-root certs/ and scripts/ folders declared above.
FROM some/base:image
COPY --from=cacerts cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY --from=cascripts inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem
```

No file needs to live inside `plugins/foo/` for this to work — `certs/` and `scripts/` stay in one
place at the repo root, shared by every component's Dockerfile. Verified against a real `buildah
bud` (not just the shell logic) with the exact `quay.io/buildah/stable` image this plugin runs.

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

### PLUGIN_TAG_SUFFIX examples

```yaml
# No suffix (default)
# tag: harel-v1.3.4  →  registry/repo/harel:v1.3.4

# Append a git SHA
PLUGIN_TAG_SUFFIX: "-abc1234"
# tag: harel-v1.3.4  →  registry/repo/harel:v1.3.4-abc1234

# Append a build number
PLUGIN_TAG_SUFFIX: "_build42"
# tag: harel-v1.3.4  →  registry/repo/harel:v1.3.4_build42
```

---


## Pipeline integration

The two plugins are designed to run as consecutive steps in the same pipeline:

1. **master-versions** calculates versions, writes changelogs, and appends every
   created tag (one per line) to the file set in `PLUGIN_OUTPUT_TAGS_FILE` (e.g. `new_tags.txt`).

2. **buildah-master-versions** reads that same file via `PLUGIN_TAGS_FILE`, resolves each tag to a
   Dockerfile on disk, and builds + pushes the corresponding Docker image.

The file is the contract between the two steps — master-versions writes it, buildah-master-versions
reads it. Both steps must share the same workspace so the file is visible to both.

```
master-versions                       buildah-master-versions
──────────────────────────────        ──────────────────────────────────────
run conventional commit script        reads new_tags.txt line by line
  → nati-v1.1.0                  ──►  nati-v1.1.0  → slug=nati  → PLUGIN_BASE_PATH/nati/Dockerfile
  → plugins-docker-v2.0.0        ──►  plugins-docker-v2.0.0 → slug=plugins-docker → PLUGIN_BASE_PATH/plugins/docker/Dockerfile
appended to new_tags.txt              builds and pushes each image via buildah
```

If `new_tags.txt` is empty (no components released), buildah-master-versions exits immediately with no builds.
