# version-file

Woodpecker CI plugin that parses a PR description, calculates semantic versions via git-cliff, writes `CHANGELOG.md` files per component, and records created tags for downstream steps — the same job `master-versions` does, but the "what version is this component at right now" question is answered by reading a **file**, not by resolving a git tag.

### What is a VERSION.txt?

`<location>/VERSION.txt` is a one-line file holding a component's current version (e.g. `1.2.0`, no `v` prefix stored on disk — that's added back on output). It's the single source of truth for "where is this component right now": no `git describe`, no branch fetch, no shallow-clone unshallowing, no PR-target-branch checkout. Whatever the file says *is* the current version, because it's just read off the working tree that's already checked out.

### What is a CHANGELOG.md?

Same as `master-versions`: a file that lives inside each component's directory and tracks every release of that component in a human-readable format. Every time a component is released, a new entry is prepended containing the version, the date, and the commit messages that triggered the release.

```
## [1.2.0] - 2024-03-15 14:30

### ✨ Features
* add OAuth2 login support

---

## [1.1.0] - 2024-02-10 09:00

### 🐛 Bug Fixes
* resolve socket timeout on large uploads
```

`version-file` additionally maintains **one repo-root `CHANGELOG.md` index** on top of the per-location ones — see [§5](#5-variables) — so there's always a single file answering "which versions does this component actually have," without opening every subdirectory.

This file is committed to the repository so the full release history is always visible in source control — no external service needed.

In a monorepo, each component has its own independent `VERSION.txt`/`CHANGELOG.md` — `nati/VERSION.txt`, `plugins/docker/VERSION.txt`, `base/argo/VERSION.txt`, and so on. Releasing one component never affects the version or changelog of another. This plugin automates it: one PR description drives all the releases, each component gets its own entry, and nothing is touched unless you explicitly named it (or it's reached by a wildcard — see [§3](#3-wildcard-expansion)).

> ### ⚠️ Required one-time Bitbucket setup
> A real release only happens on a `push` to `main` after a PR merge — and that step reads the
> PR description out of the **merge commit itself**. This only works if the repository's PR merge
> strategy is set to **Squash**, with a custom commit message template that injects the PR
> description under a `DESCRIPTION` marker. Without this, every merge "succeeds" but silently
> releases nothing. Full steps and the exact template: [§6](#6-triggering-events--manual-pull_request-and-push-merge).

---

## Contents

1. [PR body format](#1-pr-body-format)
2. [Continuation lines](#2-continuation-lines)
3. [Wildcard expansion](#3-wildcard-expansion)
4. [PLUGIN_CHANGELOG_LEVEL enforcement](#4-plugin_changelog_level-enforcement)
5. [Variables](#5-variables)
6. [Triggering events — manual, pull_request, and push (merge)](#6-triggering-events--manual-pull_request-and-push-merge)
7. [Tutorial — squash-merge setup, building the pipeline, releasing a hotfix](#7-tutorial--squash-merge-setup-building-the-pipeline-releasing-a-hotfix)
8. [Cross-referencing with changed-files](#8-cross-referencing-with-changed-files)
9. [Pipeline — standalone](#9-pipeline--standalone)
10. [Pipeline — with buildah-master-versions (optional)](#10-pipeline--with-buildah-master-versions-optional)
11. [Examples](#11-examples)


---

## 1. PR body format

Every release is triggered by a **commit line** in your PR description:

```
type[location]: description
```

> The `[` bracket immediately after the type is what makes a line a commit line.
> Without it the line is ignored — even if it starts with `feat` or `fix`.

### Semantic versioning — major, minor, patch

Every version has three numbers: `MAJOR.MINOR.PATCH` (e.g. `1.4.2`).

| Part | When it bumps | Example |
|------|--------------|---------|
| `PATCH` | A bug fix — nothing new, nothing removed | `1.4.2` → `1.4.3` |
| `MINOR` | A new feature — backwards compatible, nothing removed | `1.4.2` → `1.5.0` |
| `MAJOR` | A breaking change — existing behaviour removed or changed incompatibly | `1.4.2` → `2.0.0` |

When a part is bumped, all lower parts reset to `0`. **One exception:** a component declared in `images.txt` (CA-managed) always bumps `MAJOR` on every release, regardless of commit type — see [§5](#5-variables).

### Types (defined in `cliff.toml`)

| Type | Version bump | Notes |
|------|-------------|-------|
| `feat` | Minor | New feature or capability |
| `fix` | Patch | Bug fix, crash fix |
| `breaking` | Major | Backwards-incompatible change |
| `other` | None | Explicit no-op — no release, useful as a continuation stopper |
| `code_description` | None | Code-level description update (comments, docstrings) — no release, skip=true |
| `!` after `]` | Major | Forces major regardless of type — e.g. `fix[nati]!: msg` |

> A type not listed in `cliff.toml` `commit_parsers` is silently ignored. See `DETAILEDREADME.md` to understand how to add types.

### Location `[location]`

| What you write | What it means |
|----------------|--------------|
| `[nati]` | Component at `PLUGIN_BASE_PATH/nati/` → `nati/VERSION.txt` |
| `[plugins/docker]` | Component at `PLUGIN_BASE_PATH/plugins/docker/` → `plugins/docker/VERSION.txt` |
| `[]` | Repo root (`PLUGIN_BASE_PATH` itself) → `VERSION.txt` at the root |
| `[nati, check]` | Releases **both** `nati` and `check` from one line |
| `[*]` | Wildcard — every direct subdir of `PLUGIN_BASE_PATH` that already has a `VERSION.txt` |
| `[plugins/*]` | Wildcard — same, scoped to `PLUGIN_BASE_PATH/plugins/` |
| `[**]` | Wildcard — **every** dir anywhere under `PLUGIN_BASE_PATH` with its own `VERSION.txt`, at any depth |
| `[base/**]` | Wildcard — same, scoped to under `PLUGIN_BASE_PATH/base/` |

Unlike `master-versions`, there is no tag to look at — the location just tells the plugin which `VERSION.txt` to read and write. See [§3](#3-wildcard-expansion) for the full wildcard rules (including how `[*]`/`[**]` decide what counts as "a component" at all).

### Format rules

- Type must start at the **very beginning of the line** — no leading spaces
- Type must be **lowercase** — `FEAT[nati]: ...` is ignored
- `[location]` must immediately follow the type — no space between them
- After `]` only `:` or `!:` are valid — anything else makes the line continuation text of the previous commit
- `[location]` must not contain `[` or `]` inside it

---

## 2. Continuation lines

Identical to `master-versions`. After a commit line is matched, **every following line is collected as the commit body** until the next commit line is encountered.

A line starts a new commit only when **both** are true simultaneously:
1. It matches a `commit_parsers` pattern at position 0
2. That match is immediately followed by `[`

```
feat[nati]:
  Replace basic auth with OAuth2.
  Supports Google, GitHub, and GitLab providers.

  Blank lines are included too.

fix[check]: unrelated fix — this line ends the continuation above
```

**Using `other` to stop a continuation block cleanly:**

```
feat[nati]: big feature
  These lines are continuation text.

other[nati]: stop                ← ends continuation, no release entry
## PR Checklist                  ← continuation of "other" — also skipped
- [x] Tests pass

fix[check]: separate fix         ← new commit, clean start
```

**Unknown type (not in `cliff.toml`) becomes continuation, not a new commit:**

A line only opens a new commit if its type matches a `commit_parsers` pattern. If the type is unknown, the line is absorbed into the body of the preceding commit — even if it looks like a commit line.

```
feat[plugins/nati]: checking non cliff.toml word
checkcheck[plugins/nati]: should be continuation
```

`checkcheck` is not in `cliff.toml` `commit_parsers` → the line is **not** treated as a new commit. It becomes continuation body of the `feat` line above. The commit passed to git-cliff is:

```
feat: checking non cliff.toml word
checkcheck[plugins/nati]: should be continuation
```

Both lines land in `plugins/nati/CHANGELOG.md` under the same entry. `checkcheck[...]` is preserved verbatim in the changelog body.

**A wildcard token is exempt from `PLUGIN_CHANGELOG_LEVEL` depth-gating** — `[*]`, `[**]`, `[base/**]` have no depth of their own; whatever concrete locations they expand to are what get depth-checked (in practice this rarely matters, since a wildcard usually needs no depth restriction at all). See [§4](#4-plugin_changelog_level-enforcement).

---

## 3. Wildcard expansion

`version-file` has two wildcard families, and the difference matters:

| Selector | Expands to |
|---|---|
| `[*]` | Direct subdirs of `PLUGIN_BASE_PATH` **that already have their own indicator file** |
| `[base/*]` | Same, scoped to direct subdirs of `PLUGIN_BASE_PATH/base` |
| `[**]` | **Every** dir anywhere under `PLUGIN_BASE_PATH` with its own indicator file, at any depth — the only selector that reaches an arbitrarily deep location (e.g. a 4-level `dockerhub/org/image/tag` hardened path) in one go |
| `[base/**]` | Same, scoped to under `PLUGIN_BASE_PATH/base` |
| `[]` | Root (`PLUGIN_BASE_PATH` itself), passes through as-is — never implied by a bare `[**]` unless `images.txt` exists (see below) |

**"Indicator file" is what decides whether a directory counts as a real component at all** — by default `VERSION.txt` (a component only becomes wildcard-discoverable *after* its first release), configurable via `PLUGIN_COMPONENT_INDICATOR_FILE`. Set it to `Dockerfile` if you want a mass rotation (e.g. `breaking[**]: ...`) to also reach a component on its true first release, not just every release after the first — the moment a `Dockerfile` exists is earlier than the moment a `VERSION.txt` exists.

**`[**]` implies root too, but only in a repo using `images.txt`.** When `images.txt` exists at all, root's `VERSION.txt` *is* the shared CA version every declared image tracks — so a bare `feat[**]: rotate CA` rotates root and every declared image in one line, no separate `feat[]: ...` needed alongside it. A **scoped** `[base/**]` never implies root, regardless of `images.txt` — only the bare, unscoped `[**]` does. In a plain (non-`images.txt`) repo, `[**]` never implies root either.

```
feat[plugins/*]: bump all third-party libs to latest
```

Expands to every subdirectory of `plugins/` that already has a `VERSION.txt`, and releases each independently.

Use `PLUGIN_SCOPE_EXCLUDE_REGEX` to exclude locations you never want released (applied to every expanded location, root included):

```
PLUGIN_SCOPE_EXCLUDE_REGEX=^docs$|^scripts$
```

When a wildcard is used, the plugin prints both the pre-expansion and post-expansion commit sets:

```
>>> COMMITS TO PROCESS:
    [plugins/*]
      feat: bump all third-party libs to latest
>>> COMMITS AFTER WILDCARD EXPANSION:
    [plugins/docker]
      feat: bump all third-party libs to latest
    [plugins/kaniko]
      feat: bump all third-party libs to latest
```

---

## 4. PLUGIN_CHANGELOG_LEVEL enforcement

Every **concrete** `[location]` must match one of the declared path depths — a wildcard token itself is exempt (see [§2](#2-continuation-lines)). If any location in a multi-location line fails, **the entire line is skipped**.

| Level | Accepts | Example |
|-------|---------|---------|
| `0` | root only | `feat[]: msg` |
| `1` | top-level dirs | `feat[nati]: msg` |
| `2` | one level nested | `feat[plugins/docker]: msg` |
| `N` | depth `N` (N−1 slashes) | `feat[a/b/.../z]: msg` |

```
PLUGIN_CHANGELOG_LEVEL=1

feat[nati]: add dashboard          → ACCEPT (0 slashes)
fix[plugins/docker]: fix socket    → SKIP  (1 slash, expected 0)
feat[nati, plugins/docker]: shared → SKIP  (plugins/docker fails — whole line skipped)
feat[nati, harel]: auth update     → ACCEPT (both have 0 slashes)
```

### Allowing several depths at once

`PLUGIN_CHANGELOG_LEVEL` may be a **comma-separated list** of depths. A location is accepted
if its depth matches **any** value in the set (exact membership — not a min/max range). This is
especially useful here since `images.txt`-declared hardened images routinely live 3-4 levels deep
(`dockerhub/org/image/tag`) alongside flat top-level components:

```
PLUGIN_CHANGELOG_LEVEL=0,4

feat[]: rotate CA                                    → ACCEPT (depth 0 ∈ {0,4})
feat[dockerhub/woodpeckerci/plugin-git/2.9.3]: image  → ACCEPT (depth 4 ∈ {0,4}) — but see the
                                                         images.txt guard in §5: naming a declared
                                                         image directly like this is rejected outright.
feat[nati]: add dashboard                             → SKIP   (depth 1 ∉ {0,4})
```

---

## 5. Variables

### Required

| Variable | Description |
|----------|-------------|
| `PLUGIN_BASE_PATH` | Root directory all `[location]` paths are resolved against, and where `VERSION.txt`, the root `CHANGELOG.md` index, `images.txt`, and `Dockerfile.template` (if used) all live. |
| `PLUGIN_CHANGELOG_LEVEL` | Enforces the expected path depth of every concrete `[location]`. A single depth (`2`) or a comma-separated set of depths (`0,4`); a location is accepted if its depth is in the set. Lines with non-matching depth are skipped. If not set the plugin exits with code 1. |

### Message retrieval

Identical mechanism to `master-versions` — the plugin retrieves its own message; there's no file-path input for it. It dispatches on `CI_PIPELINE_EVENT`:

| `CI_PIPELINE_EVENT` | Source | Required variables |
|---|---|---|
| `pull_request` | Fetched from the Bitbucket Server REST API (`GET .../pull-requests/{id}`), using the PR's `description` field. | `PLUGIN_BITBUCKET_TOKEN`, `CI_FORGE_URL`, `CI_REPO_OWNER`, `CI_REPO_NAME`, `CI_COMMIT_PULL_REQUEST` |
| `manual` (default) | The `PLUGIN_MESSAGE` env var, used as-is. On a manual run the plugin loudly echoes the full message back — a banner and every line numbered between `BEGIN PLUGIN_MESSAGE` / `END PLUGIN_MESSAGE` markers — so you can see exactly what was submitted. | `PLUGIN_MESSAGE` |
| any other event (e.g. `push`) | `git log -1 --pretty=%B`. If the commit message contains a `DESCRIPTION` section, only the text after that marker is used; otherwise the full commit message is used (with a yellow `WARNING` printed). | *(none — reads local git history)* |

**Unlike `master-versions`, `PLUGIN_BITBUCKET_TOKEN` is needed only for `pull_request` events.** There is no branch-ancestry tag resolution here at all, so nothing needs an authenticated `git fetch` to see prior versions — the current version is just whatever `VERSION.txt` already says in the checked-out working tree. Don't set it for `manual`/`push` runs unless you also need it for something else in your pipeline.

### How a version is decided (four cases, kept separate)

1. **CA-managed, existing version** (`images.txt` exists, and this location is root or declared in it, and its `VERSION.txt` already has content) — **always bumps MAJOR**, regardless of commit type. The commit still has to be a recognized, non-skip type (so a stray `other[...]`-only line triggers nothing), but the type no longer decides *how much* to bump. This is what keeps every hardened image locked to the same number as root: if the bump level were still type-driven, root and a hardened image could each compute a "minor" bump independently, landing on different numbers even though both are supposed to track the one CA version.
2. **Ordinary component, existing version** (`<location>/VERSION.txt` has content, not CA-managed) — bumped forward from that value via git-cliff's usual type-driven rules, same as `master-versions` always did. A short-lived, purely local git tag (created and deleted within this one component's processing — never pushed, never fetched) is the only way this touches git at all; it exists solely to give git-cliff's `--bump` something to compute from.
3. **Brand-new location, root `VERSION.txt` exists** (`<location>/VERSION.txt` missing or empty, and `PLUGIN_BASE_PATH/VERSION.txt` has content) — **adopts the root version directly, with no bump on top of it.** A hardened image added while the CA is already at `v2.0.0` starts *at* `v2.0.0`, not `v2.1.0`. Deliberately not folded into case 2 — seeding and bumping in the same step would land one bump past the value just adopted.
4. **True first release** (no prior version, no root `VERSION.txt` to seed from) — `PLUGIN_INITIAL_TAG` (default `1.0.0`), same as `master-versions`.

**Convention for a rotation that should reach every hardened image, including brand-new ones added in the same PR:** target root *and* everything else together —
```
feat[]: rotate CA
feat[**]: rotate CA
```
Root is processed first (locations are sorted, and `""` sorts before everything), so by the time a brand-new location under `[**]` is seeded, root's `VERSION.txt` already holds *this run's* new value — the new location lands on the same number root just reached, not root's old one.

### Avoiding git-cliff's own version-detection bug

git-cliff scans a tag string for the first `X.Y.Z`-shaped substring to decide "what version is this" — which misfires when a location's own path already contains something that looks like a version (e.g. a hardened image path derived from an upstream tag, `dockerhub/woodpeckerci/plugin-git/2.9.3`). Verified directly: a tag like `...-2.9.3-v1.0.0` had a `feat` commit silently bumped to a **patch** `v1.0.1` instead of the correct **minor** `v1.1.0`, because git-cliff locked onto `2.9.3` instead of the real `1.0.0` suffix. (`master-versions` hits this same upstream bug and currently documents it as a known, unfixed limitation for its own `base/uv/0.11.29/*` components — see its `BUGS_AND_FIXES.md` §5.)

`version-file` avoids it: every git-cliff-facing name (`--tag-pattern`, the ephemeral bump tag, the `--tag` used for changelog generation) uses the location's slug with dots replaced by underscores (`2.9.3` → `2_9_3`), so only the real trailing `-v1.0.0` suffix looks like a version to git-cliff. The real, dotted name is still what's written to `VERSION.txt` and the output tags file — the sanitization is purely an internal detail of talking to git-cliff.

### `images.txt` — declaring components without hand-creating files

If `PLUGIN_BASE_PATH/images.txt` exists, `version-file` scaffolds it before anything else: for every non-comment line (a location path) lacking a `VERSION.txt`, it creates the folder and an *empty* `VERSION.txt`. That's the only thing a human does to add a component in a repo using this convention — no folder or file to create by hand. The scaffolded (empty) file is exactly what case 3 above treats as "brand new," so it seeds from root the next time this location is bumped. A no-op for any repo without an `images.txt` at its root (e.g. a builtin repo, where components are declared just by existing with their own `VERSION.txt`).

**A location declared in `images.txt` can only be reached this way — never by naming it directly.** `feat[dockerhub/woodpeckerci/plugin-git/2.9.3]: ...` fails the whole run with a hard error instead of bumping that one image, even if the same message also includes `feat[**]: ...`. Without this guard a stray direct-named commit line would silently bump one hardened image out of step with the CA and every sibling image — exactly the drift the seeded-versioning design exists to prevent. This check only ever applies to `images.txt`-declared paths, so it's a no-op in a builtin repo.

### Dockerfile generation — the other `images.txt`-repo convenience

If `PLUGIN_BASE_PATH/Dockerfile.template` exists, every processed location gets a `Dockerfile` generated from it (substituting `{{FROM_IMAGE}}`) whenever:
- it has no `Dockerfile` yet, or
- its existing `Dockerfile` starts with `# GENERATED by` (so an edit to the template propagates to every already-built image on its next bump, instead of silently going stale).

A hand-written `Dockerfile` with no such marker is the **escape hatch** (e.g. a scratch/distroless image an `inject-ca.sh`-style script can't run in) and is left untouched. A no-op in any repo without a `Dockerfile.template` (builtin repos always ship a real per-image Dockerfile already).

`{{FROM_IMAGE}}` is derived from the location path as `<PLUGIN_MIRROR_REGISTRY>/<source>/<org>/<image>:<tag>` — e.g. `dockerhub/woodpeckerci/plugin-git/2.9.3` becomes `harbor.devopstashtiot.page/outbound_images/dockerhub/woodpeckerci/plugin-git:2.9.3`. The first segment must be a known source alias (`dockerhub`, `redhat`, `codeberg`, `quay`, `ghcr` — extend `_KNOWN_SOURCE_ALIASES` in `release.py` for a new one).

### The root `CHANGELOG.md` index

Alongside the per-location `CHANGELOG.md` git-cliff writes as usual, every bump also appends one line to `PLUGIN_BASE_PATH/CHANGELOG.md`, under a `## <location>` heading (created on first use), newest bullet first. So there's always one file that answers "which versions does this component actually have" — a component born at `v2.0.0` simply has no `v1.0.0` line under its heading, because none was ever inserted for it.

**Clone settings genuinely don't matter here — more so than for `master-versions`.** There's no branch-ancestry resolution, no shallow-clone unshallowing, no PR-target-branch checkout at all: the only git operation `version-file` performs is creating and immediately deleting one local, ephemeral tag per bumped component (case 2 above), which needs nothing more than a plain local repo. `partial`, `depth`, and `tags` can be anything.

```yaml
clone:
  git:
    image: <your plugin-git image>
```

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `PLUGIN_OUTPUT_TAGS_FILE` | `""` | File to write created tags to — one per line. Always created/truncated at startup even if no tags are produced. Consumed by `buildah-master-versions` when building Docker images. |
| `PLUGIN_OUTPUT_LOCATIONS_FILE` | `""` | File to write all accepted locations to — one per line, sorted, after wildcard expansion. Always created/truncated at startup (empty if nothing qualifies). Useful for cross-referencing against actually-changed directories; see [section 8](#8-cross-referencing-with-changed-files). |
| `PLUGIN_SCOPE_EXCLUDE_REGEX` | `""` | Python regex applied to every location before processing (root included). Any matching location is skipped. Example: `^docs$\|^scripts$`. |
| `PLUGIN_VERBOSE` | `0` | `0` = minimal output, `1` = show git-cliff commands, `2` = full trace including stderr. |
| `PLUGIN_INITIAL_TAG` | `1.0.0` | Version used for the true first release of a component with no existing and no seedable version. |
| `PLUGIN_V_PREFIX` | `"true"` | `"true"` → output tags use `v` prefix (`nati-v1.0.0`). Set to `"false"` to disable — `nati-1.0.0`. `VERSION.txt` itself never stores the prefix either way. |
| `PLUGIN_MIRROR_REGISTRY` | `""` | Required only if generating Dockerfiles from a template — e.g. `harbor.devopstashtiot.page/outbound_images`. |
| `PLUGIN_COMPONENT_INDICATOR_FILE` | `VERSION.txt` | What `[*]`/`[**]` (and their `base/`-scoped forms) treat as "this directory is a real component." Set to `Dockerfile` to make a mass rotation also reach a component on its true first release. |
| `PLUGIN_CLIFF_TOML` | *(bundled)* | Path to a custom `cliff.toml`. Resolution order: (1) this variable, (2) `./cliff.toml` in working dir, (3) bundled copy in the image. |

---

## 6. Triggering events — manual, pull_request, and push (merge)

The plugin retrieves its own message — there's no explicit input step. It looks at
`CI_PIPELINE_EVENT` (a Woodpecker-provided variable) and picks one of three retrieval paths.
This section walks through what actually happens on each, end to end.

### `manual` — you trigger a run yourself

You open Woodpecker's UI (or CLI) and manually trigger a pipeline, typing the release message
into the trigger dialog's `MESSAGE` field. The `Run release (manual)` step passes it
straight through as `PLUGIN_MESSAGE: "${MESSAGE}"`. The message is used as-is
(no external calls) and — because a mistyped message is the #1 cause of a confusing "nothing
released" run — echoes it back line-numbered, between `BEGIN`/`END
PLUGIN_MESSAGE` banners, so you can see exactly what was submitted before wondering why a line
didn't match.

**When to use it:** a hotfix on a branch that never goes through a PR, or any release that
doesn't have a PR description to source from. There's no branch restriction on this trigger, so it
can run against whatever branch you're on when you trigger it — and because the current version is
read straight off `VERSION.txt` in that branch's own working tree, there's nothing extra to think
about: a hotfix branch cut from an older commit automatically has the older `VERSION.txt` content,
with no branch-ancestry lookup required at all.

### `pull_request` — every PR open/update

Fires whenever a PR is opened or updated against its target branch. The plugin fetches the PR's
**live** description directly from the Bitbucket Server REST API — not whatever the description
said when the PR was first opened.

This run computes what *would* be released and can build candidate images — but it **never**
writes `VERSION.txt`, pushes changelog commits, or creates tags. Doing so would rewrite the PR's
own source branch on every push, re-triggering the `pull_request` event and re-releasing a
brand-new component on every single build. This event exists purely to preview and validate the
release; nothing is persisted until the merge.

### `push` to the main branch (merge) — the only event that persists anything

The publish pipeline also triggers on `push`, scoped tightly: `branch: main` **and**
`evaluate: 'CI_COMMIT_MESSAGE contains "Merge pull request"'`. This is deliberately not
`pull_request_closed` — that event also fires on PR decline and PR delete, which would silently
persist stale changes for a PR that never actually merged.

By the time this fires, there is no PR context left, so the Bitbucket-API path used by
`pull_request` isn't available. The plugin instead reads the merge commit's own body via
`git log -1 --pretty=%B` and takes everything after a `DESCRIPTION` marker line. **This only
works if Bitbucket's merge commit actually contains that marker and the PR description under
it** — which is not what Bitbucket produces by default. That's the required setting below.

Once the message is retrieved, the `Run release (merge)` step computes every version, writes
`VERSION.txt` and both changelog layers, and the final `Push changelogs to Git` step commits
everything and pushes the release tags — the only point in either pipeline where anything is
actually persisted back to git.

### Required Bitbucket setting: squash merge with `DESCRIPTION` injected into the commit

For the `push` event above to see the PR description at all, this repo's Bitbucket merge
strategy must be configured to carry it into the commit that lands on `main`:

1. In Bitbucket Server/DC → repository **Settings → Pull Requests → Merge strategies**, set the
   merge strategy to **Squash** (keeps `main` at one commit per PR, matching this pipeline's
   assumption that `git log -1` on the push *is* the whole merge).
2. Customize that strategy's **commit message template** to include a `DESCRIPTION` header
   followed by the PR description variable:
   ```
   Merge pull request #${id} from ${fromRefName}

   METADATA
   Title: ${title}
   Target: ${toRepoSlug} (${toRefName})
   Source: ${fromRepoSlug} (${fromRefName})

   DESCRIPTION
   ${description}
   ```
3. Set **max commit summaries to `0`**, so the squashed source-branch commit messages aren't
   appended below `DESCRIPTION` — otherwise they get parsed too, as extra (likely garbage)
   commit lines alongside the real PR body.

**If this isn't configured:** the plugin still runs, finds no `DESCRIPTION`
marker, logs a yellow `WARNING`, and falls back to the full merge commit body — which on
Bitbucket's *default* template is just `Merge pull request #123 from feature-branch`, containing
no `[location]` lines at all. The pipeline "succeeds" and silently releases nothing on every merge.

The template's first line matters beyond `DESCRIPTION` extraction, too: the publish pipeline's
`evaluate: 'CI_COMMIT_MESSAGE contains "Merge pull request"'` guard depends on it staying
`Merge pull request #...` — changing that opening line means updating the `evaluate:` guard as
well, or the publish pipeline will never fire on a real merge.

---

## 7. Tutorial — set up Bitbucket, add the pipeline, release a hotfix

A practical, copy-and-adapt guide for a repo that wants to *use* `version-file`. Do the three
parts in order: A must be done before a merge will ever produce a release, B before any pipeline
runs at all, and C assumes A and B are already in place.

### A. One-time Bitbucket setup

You need **repository admin** rights. This is what makes the release description available to
the pipeline after a PR is merged.

1. Repo → **Repository settings** (gear icon) → **Pull Requests**.
2. Under **Merge strategies**, restrict the repo to **Squash** only, and set it as the default.
3. On the Squash strategy, turn on the custom commit message option and paste this template
   exactly:
   ```
   Merge pull request #${id} from ${fromRefName}

   METADATA
   Title: ${title}
   Target: ${toRepoSlug} (${toRefName})
   Source: ${fromRepoSlug} (${fromRefName})

   DESCRIPTION
   ${description}
   ```
4. Under **Commit summaries**, set the maximum to `0`.
5. Save, then verify: open a throwaway PR with a body like `feat[nati]: verify squash template`,
   merge it, and on `main` run `git log -1 --pretty=%B`. You should see the template above with
   your PR body under `DESCRIPTION`. If you only see `Merge pull request #123 from
   feature-branch`, the template didn't save — repeat step 3.

### B. Add the pipeline to your repo

**Secrets to create first**, in Woodpecker's repo settings → Secrets:

| Secret | Value |
|---|---|
| `bitbucket_token` | A Bitbucket HTTP access token with read access to this repo — only needed if you'll use `pull_request` events |
| `docker_username` / `docker_password` | Credentials for the registry you push built images to |

**Create `.woodpecker/pr.yml`** — runs on every PR, computes candidate versions, never touches git:

```yaml
clone:
  git:
    image: woodpeckerci/plugin-git:latest

when:
  - event: pull_request

steps:
  - name: Run release
    image: netanelzucaim123/version-file:latest
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_BITBUCKET_TOKEN:
        from_secret: bitbucket_token
      PLUGIN_CHANGELOG_LEVEL: "1"   # set to whatever depth(s) your components live at, e.g. "0,4"

  - name: Build and push plugin images
    image: netanelzucaim123/buildah-master-versions:latest
    privileged: true
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_TAGS_FILE: "new_tags.txt"
      PLUGIN_REPO: "myorg"
    secrets:
      - source: docker_username
        target: plugin_username
      - source: docker_password
        target: plugin_password
```

**Create `.woodpecker/publish.yml`** — the only pipeline that ever writes back to git:

```yaml
clone:
  git:
    image: woodpeckerci/plugin-git:latest

when:
  - event: manual
  - event: push
    branch: main
    evaluate: 'CI_COMMIT_MESSAGE contains "Merge pull request"'

steps:
  - name: Run release (manual)
    image: netanelzucaim123/version-file:latest
    when:
      - event: manual
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_MESSAGE: "${MESSAGE}"   # the text you type into Woodpecker's manual-trigger dialog
      PLUGIN_CHANGELOG_LEVEL: "1"

  - name: Run release (merge)
    image: netanelzucaim123/version-file:latest
    when:
      - event: push
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_CHANGELOG_LEVEL: "1"

  - name: Build and push plugin images
    image: netanelzucaim123/buildah-master-versions:latest
    privileged: true
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_TAGS_FILE: "new_tags.txt"
      PLUGIN_REPO: "myorg"
    secrets:
      - source: docker_username
        target: plugin_username
      - source: docker_password
        target: plugin_password

  - name: Push changelogs to Git
    image: alpine/git
    commands:
      - git config --global user.email "ci-bot@example.com"
      - git config --global user.name "CI Bot"
      - git config --global safe.directory '*'
      - find . \( -name "CHANGELOG.md" -o -name "VERSION.txt" \) -not -path "./.git/*" | xargs -r git add
      - |
        if [ -n "$${CI_COMMIT_BRANCH}" ]; then
          if ! git diff --cached --quiet; then
            git commit -m "chore(release): update VERSION.txt/CHANGELOG.md files [skip ci]"
            git push --force-with-lease origin "HEAD:$${CI_COMMIT_BRANCH}"
          fi
          for tag in $(cat new_tags.txt); do git tag -f "$tag"; done
          git push --force --tags origin
        fi
```

> `$${CI_COMMIT_BRANCH}` (double `$`) is required, not a typo — Woodpecker rewrites `${...}` in
> `commands:` itself before the shell runs. A single `$` here silently becomes an empty string.

Note `PLUGIN_BITBUCKET_TOKEN` isn't set on either `publish.yml` step above — neither `manual` nor
`push` needs it, since there's no tag/branch resolution to authenticate for (see [§5](#5-variables)).

Swap `PLUGIN_CHANGELOG_LEVEL`, `PLUGIN_REPO`, and the image names for your own values, then
test: open a throwaway PR (`pr.yml` should compute versions, touching no git state), then merge it
(`publish.yml` should push `VERSION.txt`/changelog commits and a tag to `main`). If the merge run
produces nothing, re-check part A first — a missing/incorrect squash template is the most common
cause.

### C. Release a hotfix

Use this when a bug is found in an **older** shipped version, not `main`'s current one — e.g.
`nati` is at `v2.0.0` on `main`, but the fix is for the still-in-production `v1.0.0`. The goal is
`v1.0.1`, not `v2.0.1`.

Unlike `master-versions`, there's no tag-ancestry trap to avoid here — `version-file` never looks
at git history to find "the previous version," only at whatever `VERSION.txt` says in the branch
that's actually checked out. So the only thing that matters is cutting the branch from the right
*commit*:

1. **Cut the branch from the commit where `nati/VERSION.txt` said `1.0.0`** — not from `main`'s tip:
   ```bash
   git log --oneline -- nati/VERSION.txt   # find the commit where it last read "1.0.0"
   git checkout -b hotfix/nati-v1.0.1 <that-commit-sha>
   ```
   Branching from `main`'s tip instead would carry forward `main`'s current `nati/VERSION.txt`
   content (`2.0.0`), and the release would bump from `2.0.0` instead of `1.0.0` — the file is
   just read as-is, whatever it says on the branch you're on.
2. **Make the fix and push the branch:**
   ```bash
   git commit -am "fix the bug"
   git push origin hotfix/nati-v1.0.1
   ```
3. **Trigger `publish.yml` manually** in Woodpecker: pick branch `hotfix/nati-v1.0.1`, and enter
   the release message in the trigger dialog:
   ```
   fix[nati]: patch bug found in 1.0.0
   ```
4. The plugin reads `nati/VERSION.txt` as it exists on this branch (`1.0.0`) and bumps forward
   from it — correctly producing `1.0.1`, regardless of what `main`'s own `nati/VERSION.txt` says.
5. **Verify:** `nati/VERSION.txt` on `hotfix/nati-v1.0.1` should now read `1.0.1`, and the new tag
   should be pushed to that branch (never to `main`).
6. **If the fix should also land on `main`**, open a normal PR from the hotfix branch afterward —
   that's a separate, ordinary release through the usual `pull_request`/merge flow.

---

## 8. Cross-referencing with changed-files

`PLUGIN_OUTPUT_LOCATIONS_FILE` writes every accepted location as a sorted, newline-separated list, after wildcard expansion. Because it captures all qualifying locations — including those whose commit type is `skip=true` in `cliff.toml` (e.g. `other`, `code_description`) — it acts as a full scope manifest of everything the PR author claimed to touch, regardless of whether a release was produced.

The [`changed-files`](../changed-files/) plugin writes the set of directories that actually changed in the push. The [`master-versions-vs-changed-files`](../master-versions-vs-changed-files/) plugin then compares the two and reports mismatches:

- **Changed but not declared** — a directory changed on disk but no `[location]` in the PR body covers it
- **Declared but not changed** — a `[location]` appears in the PR body but no files under it actually changed

> No "fetch PR body" step is needed — `version-file` retrieves its own message (see [§6 Triggering events](#6-triggering-events--manual-pull_request-and-push-merge)), so it only needs the usual `PLUGIN_BITBUCKET_TOKEN`/`PLUGIN_MESSAGE` depending on the triggering event.

```yaml
steps:
  - name: Get changed dirs
    image: netanelzucaim123/changed-files:latest
    settings:
      output_file: changed_dirs.txt
      output_type: dirs
      folder_depth: 1

  - name: Run release
    image: netanelzucaim123/version-file:latest
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_CHANGELOG_LEVEL: "1"
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_OUTPUT_LOCATIONS_FILE: "release_locations.txt"
      PLUGIN_BITBUCKET_TOKEN:
        from_secret: bitbucket_token

  - name: Check scopes vs changes
    image: netanelzucaim123/master-versions-vs-changed-files:latest
    settings:
      master_versions_locations_file: release_locations.txt
      changed_dirs_file: changed_dirs.txt
      fail_on_mismatch: false
```

> Set `fail_on_mismatch: true` to fail the pipeline when the PR description and the actual changed directories do not match exactly.

---

## 9. Pipeline — standalone

Use `version-file` on its own when you only need versioning and changelogs — no Docker image builds involved.

> No "fetch PR body" step is needed — `version-file` retrieves its own message (see [§6 Triggering events](#6-triggering-events--manual-pull_request-and-push-merge)): the Bitbucket API for `pull_request` events, `PLUGIN_MESSAGE` for `manual` runs, or the merge commit for a `push`. Clone settings don't matter at all — see [§5](#5-variables).

```yaml
steps:
  - name: Run release
    image: netanelzucaim123/version-file:latest
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_CHANGELOG_LEVEL: "1"
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_BITBUCKET_TOKEN:
        from_secret: bitbucket_token

  - name: Push changelogs and tags
    image: alpine/git
    commands:
      - git config user.email "ci@example.com"
      - git config user.name "CI"
      - find . \( -name "CHANGELOG.md" -o -name "VERSION.txt" \) -not -path "./.git/*" | xargs -r git add
      - |
        if ! git diff --cached --quiet; then
          git commit -m "chore: update changelogs [skip ci]"
          git push --force-with-lease origin "HEAD:$${CI_COMMIT_BRANCH}"
        fi
        for tag in $(cat new_tags.txt); do git tag -f "$tag"; done
        git push --force --tags origin
```

> `--force-with-lease` on the branch push (not plain `--force`): it only overwrites if the remote branch still matches what this workspace last saw, so a concurrent push to the same branch is rejected instead of silently discarded. `$${CI_COMMIT_BRANCH}` uses Woodpecker's `$$` escape so the literal `${CI_COMMIT_BRANCH}` reaches the shell instead of being substituted away by Woodpecker first — see the root `CLAUDE.md`'s Bitbucket push-access gotcha for the same escaping rule.

---

## 10. Pipeline — with buildah-master-versions (optional)

> **Only add this step if your repository contains Dockerfiles you want to build and push.**
> If you only do versioning and changelogs, the previous section is all you need.

When each component has a `Dockerfile` (hand-written, or generated from `Dockerfile.template` — see [§5](#5-variables)), `buildah-master-versions` reads the tags file produced by `version-file` and builds + pushes the corresponding Docker image for each tag.

```
version-file                            buildah-master-versions
──────────────────────────────          ──────────────────────────────────────────
parse retrieved message                 reads new_tags.txt line by line
  → nati-1.1.0                     ──►  nati-1.1.0       → PLUGIN_BASE_PATH/nati/Dockerfile
  → plugins-docker-2.0.0           ──►  plugins-docker-2.0.0 → PLUGIN_BASE_PATH/plugins/docker/Dockerfile
appended to new_tags.txt                builds and pushes each image via buildah
```

```yaml
steps:
  - name: Run release
    image: netanelzucaim123/version-file:latest
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_CHANGELOG_LEVEL: "1"
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_BITBUCKET_TOKEN:
        from_secret: bitbucket_token

  - name: Push changelogs and tags
    image: alpine/git
    commands:
      - git config user.email "ci@example.com"
      - git config user.name "CI"
      - find . \( -name "CHANGELOG.md" -o -name "VERSION.txt" \) -not -path "./.git/*" | xargs -r git add
      - |
        if ! git diff --cached --quiet; then
          git commit -m "chore: update changelogs [skip ci]"
          git push --force-with-lease origin "HEAD:$${CI_COMMIT_BRANCH}"
        fi
        for tag in $(cat new_tags.txt); do git tag -f "$tag"; done
        git push --force --tags origin

  - name: Build and push images
    image: netanelzucaim123/buildah-master-versions:latest
    settings:
      base_path: .
      tags_file: new_tags.txt
      repo: myorg
    secrets:
      - source: docker_username
        target: plugin_username
      - source: docker_password
        target: plugin_password
```

---

## 11. Examples

### Single component — minor bump

```
feat[nati]: add sidebar with user stats
```
→ `nati/VERSION.txt` → `1.1.0`, `nati/CHANGELOG.md` and the root `CHANGELOG.md` both updated.

---

### Nested component — patch bump

```
fix[plugins/docker]: increase read deadline to 30s
```
→ `plugins/docker/VERSION.txt` → `1.0.1`.

---

### Repo root release

```
feat[]: add woodpecker pipeline definition
```
→ `VERSION.txt` at the root → `1.0.0`.

---

### Multiple components from one line

```
feat[nati, check, base/argo]: centralise JWT validation
```
→ Three independent releases: `nati` → `1.1.0`, `check` → `1.1.0`, `base/argo` → `1.1.0`.

---

### Breaking change — two ways to force major

```
breaking[nati]: remove /v1 endpoints
feat[nati]!: replace REST with gRPC interface
```
Both produce a major bump (for a non-CA-managed component — a CA-managed one is *always* major regardless, see below).

---

### CA-managed rotation — root and every hardened image together

Given `images.txt` lists `dockerhub/woodpeckerci/plugin-git/2.9.3` and root is currently at `v2.0.0`:

```
feat[]: rotate CA
feat[**]: rotate CA
```
→ Root bumps to `v3.0.0` (CA-managed → forced major, regardless of `feat`). `dockerhub/woodpeckerci/plugin-git/2.9.3` — already released before, also CA-managed — bumps to `v3.0.0` too, staying in lock-step with root, not to `v2.9.4`/`v2.10.0` (what a type-driven bump would have computed independently).

---

### New hardened image added in the same rotation

Same setup, but `dockerhub/newco/newimage/1.0.0` is a brand-new line just added to `images.txt`, with no prior `VERSION.txt` content:

```
feat[]: rotate CA
feat[**]: rotate CA
```
→ Root bumps `v2.0.0` → `v3.0.0` first (locations are processed in sorted order, root first). `dockerhub/newco/newimage/1.0.0` seeds directly at `v3.0.0` — root's *new* value, not `v2.1.0` and not root's *old* `v2.0.0`.

---

### Multi-line changelog entry

```
feat[nati]:
  Replace basic auth with OAuth2.
  Supports Google, GitHub, and GitLab providers.
  Adds token refresh logic and session expiry handling.

fix[check]: unrelated fix — ends the continuation above
```

The full multi-line text becomes the `nati` changelog entry.

---

### Wildcard — release all plugins at once

```
feat[plugins/*]: bump all third-party libs to latest
```
→ Expands to every subdirectory of `plugins/` that already has a `VERSION.txt`, and releases each independently.

---

### Mixed PR body — multiple components, types, and prose

```
feat[nati]: add avatar upload
fix[plugins/docker]: lock shared map access
breaking[base/argo]!: rename all env vars to SNAKE_CASE
other[]: explicit no-op at root
code_description[nati]: improve inline comments

This is a checklist:
- [x] Tests pass
- [x] Docs reviewed
```

Result:
- `nati` → minor bump (`feat` wins; `code_description` is skip=true and adds no release on its own)
- `plugins/docker` → patch bump
- `base/argo` → major bump (`breaking` + `!`)
- root → no release (`other` is skip=true)
- Checklist lines → continuation of `other[]` → also skipped
