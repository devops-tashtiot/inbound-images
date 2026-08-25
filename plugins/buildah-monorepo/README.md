# buildah-monorepo

A Woodpecker CI plugin that builds and pushes Docker images for every directory listed in a changed-files manifest, using **buildah** with **rootless isolation** and **overlay storage** exclusively.

---

## What It Does

Given a file containing a list of directory paths (one per line), the plugin:

1. Iterates over each path
2. Finds the `Dockerfile` inside that directory
3. Builds the image using `buildah bud --isolation rootless --storage-driver overlay`
4. Tags the image as `REGISTRY/[REPO/]path:TAG`
5. Pushes all tags from `PLUGIN_TAGS`

### Example

`changed_dirs.txt`:
```
plugins/harel
plugins/lagziel
base/argo
```

With `PLUGIN_REGISTRY=10.89.0.1:5000`, `PLUGIN_REPO=check`, `PLUGIN_TAGS=abc1234,latest`:

```
10.89.0.1:5000/check/plugins/harel:abc1234
10.89.0.1:5000/check/plugins/harel:latest

10.89.0.1:5000/check/plugins/lagziel:abc1234
10.89.0.1:5000/check/plugins/lagziel:latest

10.89.0.1:5000/check/base/argo:abc1234
10.89.0.1:5000/check/base/argo:latest
```

### Example with `PLUGIN_BASE_PATH`

`changed_dirs.txt` (produced by `changed-files` with `PLUGIN_BASE_PATH=nati/check` stripping):
```
root/sagi
core
```

With `PLUGIN_BASE_PATH=nati/check`, `PLUGIN_REGISTRY=10.89.0.1:5000`, `PLUGIN_REPO=check`, `PLUGIN_TAGS=abc1234`:

| Path in file | Dockerfile resolved at | Image pushed |
|---|---|---|
| `root/sagi` | `nati/check/root/sagi/Dockerfile` | `10.89.0.1:5000/check/root/sagi:abc1234` |
| `core` | `nati/check/core/Dockerfile` | `10.89.0.1:5000/check/core:abc1234` |

The base path is used only for locating files on disk — image names are always derived from the bare path in the changed file.

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `PLUGIN_CHANGED_FILE` | Path to a newline-separated file of directories to build (e.g. `changed_dirs.txt`) |
| `PLUGIN_USERNAME` | Registry username |
| `PLUGIN_PASSWORD` | Registry password |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `PLUGIN_REGISTRY` | `index.docker.io` | Target registry |
| `PLUGIN_REPO` | `""` | Namespace/repository prefix. Empty = path goes directly under registry |
| `PLUGIN_TAGS` | *(none)* | Comma-separated list of tags to push (e.g. `abc1234,latest,prod`). If unset and no `.tags` file, falls back to `latest` |
| `PLUGIN_DOCKERFILE` | `Dockerfile` | Dockerfile filename to look for inside each directory |
| `PLUGIN_BASE_PATH` | `""` | Path prefix prepended to every directory from `PLUGIN_CHANGED_FILE` when locating the Dockerfile. Does **not** affect image naming — the tag still uses the bare path from the file. |
| `PLUGIN_CONTEXT` | `$PWD` | Root build context — each path is resolved relative to this |
| `PLUGIN_LOG_LEVEL` | `info` | buildah log verbosity (`debug`, `info`, `warn`, `error`) |
| `PLUGIN_DRY_RUN` | `""` | `"true"` → build only, skip push |
| `PLUGIN_INSECURE` | `""` | `"true"` → `--tls-verify=false` for login and push |
| `PLUGIN_INSECURE_PULL` | `""` | `"true"` → `--tls-verify=false` when pulling base images during build |
| `PLUGIN_CACHE` | `""` | `"true"` → enable `--layers` layer caching |
| `PLUGIN_CACHE_REPO` | `""` | Remote cache source (`--cache-from`) |
| `PLUGIN_CACHE_TTL` | `""` | Cache TTL passed to `--cache-ttl` |
| `PLUGIN_BUILD_ARGS` | `""` | Comma-separated build args: `KEY=VALUE,KEY2=VALUE2` |
| `PLUGIN_BUILD_ARGS_FROM_ENV` | `""` | Comma-separated env var names to forward as `--build-arg` |
| `PLUGIN_AUTO_TAG` | `""` | `"true"` → generate SemVer tags from `CI_COMMIT_TAG` into `.tags` |
| `PLUGIN_ENV_FILE` | `""` | Path to a file of `KEY=VALUE` pairs to load before running |

### Image Reference Format

| `PLUGIN_REPO` | Path in changed file | Result |
|---------------|----------------------|--------|
| `check` | `plugins/harel` | `REGISTRY/check/plugins/harel:TAG` |
| `""` | `plugins/harel` | `REGISTRY/plugins/harel:TAG` |
| `check` | `base/argo` | `REGISTRY/check/base/argo:TAG` |

---

## Storage and Isolation

Both flags are **hardcoded** — they cannot be overridden:

```sh
buildah bud --isolation rootless --storage-driver overlay ...
```

- `--isolation rootless` — uses rootless OCI isolation; no real root or privileged namespaces required inside the build
- `--storage-driver overlay` — uses the overlay filesystem driver backed by `fuse-overlayfs`; no `vfs` or other drivers are ever used

---

## Typical Pipeline Usage

```yaml
- name: Get changed dirs
  image: netanelzucaim123/changed-files:latest
  environment:
    PLUGIN_OUTPUT_FILE: "changed_dirs.txt"
    PLUGIN_OUTPUT_TYPE: "dirs"
    PLUGIN_FOLDER_DEPTH: "2"
    PLUGIN_DEDUP: "true"
    PLUGIN_INCLUDE_FILES_REGEX: "^(plugins|base)"
    PLUGIN_EXCLUDE_FILES_REGEX: '\.md$'

- name: Build changed dirs
  image: netanelzucaim123/buildah-monorepo:latest
  privileged: true
  environment:
    PLUGIN_CHANGED_FILE: "changed_dirs.txt"
    PLUGIN_REGISTRY: "10.89.0.1:5000"
    PLUGIN_REPO: "myrepo"
    PLUGIN_INSECURE: "true"
    PLUGIN_USERNAME: "user"
    PLUGIN_PASSWORD: "pass"
  commands:
    - export PLUGIN_TAGS="${CI_COMMIT_SHA:0:7},latest"
    - /app/plugin.sh
```

---

## Difference vs `buildah-master-versions`

Both plugins use buildah to build and push images in a monorepo. They solve **different problems** and are designed to run at **different points** in the pipeline.

| | `buildah-monorepo` | `buildah-master-versions` |
|---|---|---|
| **Input** | `PLUGIN_CHANGED_FILE` — a file of directory paths | `PLUGIN_TAGS_FILE` / `PLUGIN_TAGS` — a file of versioned tags |
| **Driven by** | What files **changed** in the commit | What components were **released** by `master-versions` |
| **Tag source** | External — you supply the tag (e.g. commit SHA via `PLUGIN_TAGS`) | Embedded in the tag name itself (e.g. `nati-1.2.0_abc1234`) |
| **Path resolution** | Direct — the path in the file is used as-is | Reverse slug lookup — `nati-1.2.0` → slug `nati` → finds `PLUGIN_BASE_PATH/nati/Dockerfile` |
| **Versioning** | No version awareness — any tag string works | Semantically versioned — slug encodes the component path |
| **Pipeline position** | Runs **before** `master-versions` (build every changed dir on every commit) | Runs **after** `master-versions` (build only what got a new semver release) |
| **Use case** | Continuous delivery — push a SHA-tagged image on every change | Release delivery — push a semver-tagged image when a version is cut |
| **Multiple tags** | Yes — comma-separated `PLUGIN_TAGS` | Yes — `PLUGIN_ALIASES` |

### When to use which

> **Recommendation: prefer `buildah-master-versions` whenever possible.**
> It does everything `buildah-monorepo` does for image delivery, and additionally:
> - Creates a **semantic version Git tag** per component (`nati-1.2.0`, `plugins-docker-2.0.0`, ...)
> - Generates and commits a **`CHANGELOG.md`** per component, derived directly from the PR description
>
> This means every image in the registry has a traceable version number and a human-readable history of what changed and why — which `buildah-monorepo` cannot provide.

> **Warning: using `buildah-monorepo` is not best practice for long-running registries.**
> Every commit that touches any component pushes a new SHA-tagged image. Over time this accumulates a large number of images with no semantic meaning, no changelog, and no clear lifecycle — making it hard to know what to keep and what to delete.
>
> If you do use this plugin, enforce a **hard retention policy** on your registry:
> - Delete images older than N days automatically
> - Keep only the last N tags per repository
> - Never use it as the source of truth for production deployments — use semver tags from `buildah-master-versions` for that

- Use **`buildah-monorepo`** when you need a lightweight, commit-by-commit image build with no versioning — for example, a staging environment where you just want the latest SHA-tagged image of every changed component without managing semver or changelogs.

- Use **`buildah-master-versions`** (preferred) when you want proper release management: each component gets a semantic version tag, a `CHANGELOG.md` entry, and a versioned image — all driven from the PR description in one pipeline run.

Both plugins can coexist in the same pipeline — `buildah-monorepo` runs early (after changed-files detection) for continuous SHA-tagged delivery to staging, while `buildah-master-versions` runs at the end for versioned, documented release images.
