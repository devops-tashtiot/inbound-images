# version-file

File-based version orchestrator — the same plugin for a "builtin" repo (each component bumped
independently by its own commit message) and a "hardened" repo (images declared in `images.txt`,
versioned off one shared root `VERSION.txt`). It replaces `master-versions`'s git-tag version
resolution (`git describe`, branch-ancestry fetch/checkout, shallow-clone unshallowing) with a
plain file: **the current version of any component is whatever `<location>/VERSION.txt` says**,
full stop. No git history is read to determine "where are we now."

## Why file-based instead of tags

`master-versions` had to resolve "what's the latest tag for this component" via `git describe`
against the right branch — which meant fetching the target branch, unshallowing a shallow clone,
and checking out/restoring around the version calculation. All of that existed only to answer one
question a file answers instantly. `version-file` still uses **git-cliff** to compute *how much* to
bump (patch/minor/major from the commit type/`!`) — that logic is untouched — but the *base* it
bumps from is read off disk, and a purely local, ephemeral git tag (created and deleted within a
single component's processing, never pushed, never fetched) is the only way it touches git at all.

## Commit message format

Identical to `master-versions`:

```
type[location]: description
type[location]!: description        # bang forces a major bump
type[loc1, loc2]: description       # multiple locations, one commit
```

Wildcards:

| Selector | Expands to |
|---|---|
| `[*]` | direct subdirs of `PLUGIN_BASE_PATH` |
| `[base/*]` | direct subdirs of `PLUGIN_BASE_PATH/base` |
| `[**]` | **every** dir anywhere under `PLUGIN_BASE_PATH` that has its own `VERSION.txt` — the only selector that reaches arbitrarily deep locations (e.g. a 4-level `dockerhub/org/image/tag` hardened path) in one go |
| `[base/**]` | same, scoped to under `PLUGIN_BASE_PATH/base` |
| `[]` | root (`PLUGIN_BASE_PATH` itself) |

`PLUGIN_CHANGELOG_LEVEL` depth-gates concrete locations as before, but never a wildcard token
itself — `[**]` isn't "depth 1", it's however deep what it expands to actually is.

## How a version is decided (three cases, kept separate)

1. **Existing component** (`<location>/VERSION.txt` has content) — bumped forward from that value
   via git-cliff, same as `master-versions` always did.
2. **Brand-new component, root `VERSION.txt` exists** (`<location>/VERSION.txt` missing or empty,
   and `PLUGIN_BASE_PATH/VERSION.txt` has content) — **adopts the root version directly, with no
   bump on top of it.** A hardened image added while the CA is already at `v2.0.0` starts *at*
   `v2.0.0`, not `v2.1.0`. This is deliberately not folded into case 1 — seeding and bumping in the
   same step would land one bump past the value just adopted.
3. **True first release** (no prior version, no root `VERSION.txt` to seed from) — `PLUGIN_INITIAL_TAG`
   (default `1.0.0`), same as `master-versions`.

**Convention for a rotation that should reach every hardened image, including brand-new ones added
in the same PR:** target root *and* everything else together —
```
feat[]: rotate CA
feat[**]: rotate CA
```
Root is processed first (locations are sorted, and `""` sorts before everything), so by the time
a brand-new location under `[**]` is seeded, root's `VERSION.txt` already holds *this run's* new
value — the new location lands on the same number root just reached, not root's old one.

**A location declared in `images.txt` can only be reached this way — never by naming it directly.**
`feat[dockerhub/woodpeckerci/plugin-git/2.9.3]: ...` fails the whole run with a hard error instead
of bumping that one image, even if the same message also includes `feat[**]: ...`. Without this
guard a stray direct-named commit line would silently bump one hardened image out of step with the
CA and every sibling image — exactly the drift the seeded-versioning design exists to prevent. This
check only ever applies to `images.txt`-declared paths, so it's a no-op in a builtin repo.

## `images.txt` — declaring components without hand-creating files

If `PLUGIN_BASE_PATH/images.txt` exists, `version-file` scaffolds it before anything else: for
every non-comment line (a location path) lacking a `VERSION.txt`, it creates the folder and an
*empty* `VERSION.txt`. That's the only thing a human does to add a component in a repo using this
convention — no folder or file to create by hand. The scaffolded (empty) file is exactly what case
2 above treats as "brand new," so it seeds from root the next time this location is bumped. A
no-op for any repo without an `images.txt` at its root (e.g. a builtin repo, where components are
declared just by existing with their own `VERSION.txt`).

## Dockerfile generation — the other `images.txt`-repo convenience

If `PLUGIN_BASE_PATH/Dockerfile.template` exists, every processed location gets a `Dockerfile`
generated from it (substituting `{{FROM_IMAGE}}`) whenever:
- it has no `Dockerfile` yet, or
- its existing `Dockerfile` starts with `# GENERATED by` (so an edit to the template propagates to
  every already-built image on its next bump, instead of silently going stale).

A hand-written `Dockerfile` with no such marker is the **escape hatch** (e.g. a scratch/distroless
image `inject-ca.sh`-style scripts can't run in) and is left untouched. A no-op in any repo without
a `Dockerfile.template` (builtin repos always ship a real per-image Dockerfile already).

`{{FROM_IMAGE}}` is derived from the location path as
`<PLUGIN_MIRROR_REGISTRY>/<source>/<org>/<image>:<tag>` — e.g.
`dockerhub/woodpeckerci/plugin-git/2.9.3` becomes
`harbor.devopstashtiot.page/outbound_images/dockerhub/woodpeckerci/plugin-git:2.9.3`. The first
segment must be a known source alias (`dockerhub`, `redhat`, `codeberg`, `quay`, `ghcr` — extend
`_KNOWN_SOURCE_ALIASES` in `release.py` for a new one).

## The root `CHANGELOG.md` index

Alongside the per-location `CHANGELOG.md` git-cliff writes as usual, every bump also appends one
line to `PLUGIN_BASE_PATH/CHANGELOG.md`, under a `## <location>` heading (created on first use),
newest bullet first. So there's always one file that answers "which versions does this component
actually have" — a component born at `v2.0.0` simply has no `v1.0.0` line under its heading,
because none was ever inserted for it.

## Environment variables

| Var | Required | Default | Meaning |
|---|---|---|---|
| `PLUGIN_BASE_PATH` | yes | — | root directory; all `[location]` paths resolve relative to this |
| `PLUGIN_CHANGELOG_LEVEL` | yes | — | allowed depth(s) for concrete locations, e.g. `0,4` |
| `PLUGIN_MESSAGE` | for `manual` events | — | the commit message to parse |
| `PLUGIN_BITBUCKET_TOKEN` | for `pull_request` events | — | fetches the PR description from Bitbucket |
| `PLUGIN_INITIAL_TAG` | no | `1.0.0` | version used for a true first release |
| `PLUGIN_V_PREFIX` | no | `true` | prefix `VERSION.txt`-derived tags with `v` |
| `PLUGIN_MIRROR_REGISTRY` | only if generating Dockerfiles | — | e.g. `harbor.devopstashtiot.page/outbound_images` |
| `PLUGIN_SCOPE_EXCLUDE_REGEX` | no | — | regex excluding matched locations (applies post-wildcard-expansion too) |
| `PLUGIN_OUTPUT_TAGS_FILE` | no | — | one `<tag_prefix><version>` per bumped component, per line |
| `PLUGIN_OUTPUT_LOCATIONS_FILE` | no | — | one location per line, after wildcard expansion |
| `PLUGIN_CLIFF_TOML` | no | bundled `cliff.toml` | override the bump-rules config |
| `PLUGIN_VERBOSE` | no | `0` | `1`/`2` for more git-cliff command tracing |

## Tests

```bash
cd plugins/version-file && python3 test_release.py
```
