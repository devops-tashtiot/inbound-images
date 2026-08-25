# master-versions

Woodpecker CI plugin that parses a PR description, calculates semantic versions via git-cliff, writes `CHANGELOG.md` files per component, and records created tags for downstream steps.

### What is a CHANGELOG.md?

A `CHANGELOG.md` is a file that lives inside each component's directory and tracks every release of that component in a human-readable format. Every time a component is released, a new entry is prepended to its `CHANGELOG.md` containing the version, the date, and the commit messages that triggered the release.

```
## [nati-1.2.0] - 2024-03-15 14:30

### ✨ Features
* add OAuth2 login support

---

## [nati-1.1.0] - 2024-02-10 09:00

### 🐛 Bug Fixes
* resolve socket timeout on large uploads
```

This file is committed to the repository so the full release history is always visible in source control — no external service needed.

In a monorepo, each component has its own independent `CHANGELOG.md` and its own version — `nati/CHANGELOG.md`, `plugins/docker/CHANGELOG.md`, `base/argo/CHANGELOG.md`, and so on. Releasing one component never affects the version or changelog of another. Managing all of this manually across many components is error-prone and tedious. This plugin automates it: one PR description drives all the releases, each component gets its own entry, and nothing is touched unless you explicitly named it.

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

When a part is bumped, all lower parts reset to `0`.

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
| `[nati]` | Component at `PLUGIN_BASE_PATH/nati/` → tag `nati-1.0.0` |
| `[plugins/docker]` | Component at `PLUGIN_BASE_PATH/plugins/docker/` → tag `plugins-docker-1.0.0` |
| `[]` | Repo root (`PLUGIN_BASE_PATH` itself) → tag `1.0.0` |
| `[nati, check]` | Releases **both** `nati` and `check` from one line |
| `[*]` | Wildcard — expands to all direct subdirs of `PLUGIN_BASE_PATH` |
| `[plugins/*]` | Wildcard — expands to all subdirs of `PLUGIN_BASE_PATH/plugins/` |

Slashes in the location become hyphens in the tag:

| Location | Tag |
|----------|-----|
| `nati` | `nati-1.1.0` |
| `plugins/docker` | `plugins-docker-1.0.1` |
| `base/argo` | `base-argo-2.0.0` |
| *(empty)* | `1.0.0` |

### Format rules

- Type must start at the **very beginning of the line** — no leading spaces
- Type must be **lowercase** — `FEAT[nati]: ...` is ignored
- `[location]` must immediately follow the type — no space between them
- After `]` only `:` or `!:` are valid — anything else makes the line continuation text of the previous commit
- `[location]` must not contain `[` or `]` inside it

---

## 2. Continuation lines

After a commit line is matched, **every following line is collected as the commit body** until the next commit line is encountered.

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

A line only opens a new commit if its type matches a `commit_parsers` pattern. If the type is unknown, `_match_line` returns nothing and the line is absorbed into the body of the preceding commit — even if it looks like a commit line.

```
feat[plugins/nati]: checking non cliff.toml word
checkcheck[plugins/nati]: should be continuation
```

`checkcheck` is not in `cliff.toml` `commit_parsers` → `_match_line` returns nothing → the line is **not** treated as a new commit. It becomes continuation body of the `feat` line above. The commit passed to git-cliff is:

```
feat: checking non cliff.toml word
checkcheck[plugins/nati]: should be continuation
```

Both lines land in `plugins/nati/CHANGELOG.md` under the same entry. `checkcheck[...]` is preserved verbatim in the changelog body.

---

## 3. Wildcard expansion

`[*]` expands to all direct subdirectories of `PLUGIN_BASE_PATH`.
`[base/*]` expands to all subdirectories of `PLUGIN_BASE_PATH/base/`.

```
feat[plugins/*]: bump all third-party libs to latest
```

Expands to every subdirectory of `plugins/` and releases each independently.

Use `PLUGIN_SCOPE_EXCLUDE_REGEX` to exclude folders you never want released:

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

Every `[location]` must match one of the declared path depths. If any location in a multi-location line fails, **the entire line is skipped**.

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
if its depth matches **any** value in the set (exact membership — not a min/max range). This lets
a single run release components living at different nesting levels — e.g. flat plugins at depth 2
alongside deeply-nested base images at depth 4.

```
PLUGIN_CHANGELOG_LEVEL=2,4

feat[plugins/docker]: fix socket             → ACCEPT (depth 2 ∈ {2,4})
feat[base/uv/0.11.29/python-310]: uv image   → ACCEPT (depth 4 ∈ {2,4})
feat[nati]: add dashboard                    → SKIP   (depth 1 ∉ {2,4})
feat[base/infra/x]: rules                    → SKIP   (depth 3 ∉ {2,4})
```

---

## 5. Variables

### Required

| Variable | Description |
|----------|-------------|
| `PLUGIN_BASE_PATH` | Root directory all `[location]` paths are resolved against. Getting this wrong silently breaks tag names, CHANGELOG paths, and directory resolution. When in doubt use `"."` and write full relative paths in `[]`. |
| `PLUGIN_CHANGELOG_LEVEL` | Enforces the expected path depth of every `[location]`. A single depth (`2`) or a comma-separated set of depths (`2,3,4`); a location is accepted if its depth is in the set. Lines with non-matching depth are skipped. If not set the plugin exits with code 1. |

### Message retrieval

The plugin retrieves the message to parse itself — there's no file-path input for it. It dispatches on `CI_PIPELINE_EVENT` (a Woodpecker-provided variable, not user-set):

| `CI_PIPELINE_EVENT` | Source | Required variables |
|---|---|---|
| `pull_request` | Fetched from the Bitbucket Server REST API (`GET .../pull-requests/{id}`), using the PR's `description` field. | `PLUGIN_BITBUCKET_TOKEN`, `CI_FORGE_URL`, `CI_REPO_OWNER`, `CI_REPO_NAME`, `CI_COMMIT_PULL_REQUEST` |
| `manual` (default) | The `PLUGIN_MESSAGE` env var, used as-is. On a manual run the plugin loudly echoes the full message back — a banner and every line numbered between `BEGIN PLUGIN_MESSAGE` / `END PLUGIN_MESSAGE` markers (tabs shown as `\t`) — so you can see exactly what was submitted. This is the fastest way to spot a mistyped message (e.g. a leading space or a pasted image reference) that would otherwise make every line silently `IGNORED`. | `PLUGIN_MESSAGE` |
| any other event (e.g. `push`) | `git log -1 --pretty=%B`. If the commit message contains a `DESCRIPTION` section (the custom merge-commit template — see the "Pipeline Integration" notes), only the text after that marker is used; otherwise the full commit message is used. | *(none — reads local git history)* |

The plugin exits with code 1 if the message can't be determined (e.g. a missing required variable, or an empty `PLUGIN_MESSAGE` on a manual run). Whatever message is retrieved is also written to `pr_body.txt` in the working directory, so later pipeline steps that grep it for override values (e.g. `PLUGIN_BASE_PATH=`) keep working.

**`PLUGIN_BITBUCKET_TOKEN` is also used for tag resolution.** Before processing any component, the plugin does an authenticated `git fetch` of the resolved branch so git's tag auto-follow pulls the existing version tags (the CI clone uses `tags: false`, so the workspace starts with none). The plugin's own step image has no Bitbucket credentials of its own, so the token is sent as an `Authorization: Bearer <token>` header via `git -c http.extraHeader=…` (the only scheme Bitbucket DC HTTP tokens accept). Without it the fetch 401s, no tags are visible, and every component is mistakenly treated as a first release (recreating `…-v1.0.0` instead of bumping). Set `PLUGIN_BITBUCKET_TOKEN` on any event where you want correct version bumps, not just `pull_request`.

**Works with the clone's `tags: true` OR `tags: false`.** Version resolution is always scoped to the correct branch, regardless of how many tags the clone brought into the workspace:

- git-cliff's bump is invoked with `--use-branch-tags`, so it only considers tags reachable from the checked-out `HEAD`. With `tags: false` only ancestry tags are present anyway (no-op); with `tags: true` (every tag from every branch present) it still resolves correctly — a `fix` on a hotfix cut from `v1.0.0` bumps to `v1.0.1`, never `v2.0.1` from an unrelated mainline `v2.0.0`. **No tags are ever deleted.**
- Because `--use-branch-tags` looks at the checked-out branch, a `pull_request` run must resolve against its **target** branch, not the PR's own branch. So for `pull_request` events the plugin temporarily `git checkout`s the target branch (`CI_COMMIT_TARGET_BRANCH`), calculates every version there, then checks back to the PR branch before writing any `CHANGELOG.md` (so the changelog files persist for the push step). Non-PR runs calculate directly on the current branch.

**It doesn't matter what you set `partial`, `depth`, or `tags` to on the clone step (or whether you set them at all) — any combination works.** See `DETAILEDREADME.md` for why.

```yaml
clone:
  git:
    image: <your plugin-git image>
```

**Any git command that could affect a computed version fails the run instead of degrading silently.** Earlier versions of the plugin logged a `WARNING` and fell back to a possibly-wrong ref (e.g. `HEAD` instead of the resolved branch, or the PR's own branch instead of its target) when a fetch/checkout failed — which could silently compute a version from the wrong base. It now exits with code 1 in every case where that would happen:

| Failure | Old behavior | New behavior |
|---|---|---|
| `git rev-parse --is-shallow-repository` fails | assumed "not shallow" | exit 1 |
| Branch fetch (`git fetch origin <branch>:refs/remotes/origin/<branch>`) fails | fell back to resolving against `HEAD` | exit 1 |
| Unshallow fetch fails (bare local run on a shallow clone) | previously an unrelated `NameError` crash — now a proper diagnosed failure | exit 1 |
| PR target-branch checkout fails | fell back to computing versions against the PR's own branch | exit 1 |
| Restoring the original checkout after PR version calculation fails | unchecked — Phase B could write `CHANGELOG.md` files against the wrong tree | exit 1 |
| `git-cliff --bump` itself exits non-zero | silently treated the same as "no releasable commits" (SKIP) | exit 1 |
| One component's `CHANGELOG.md` write fails in Phase B | logged an error but the run still exited 0 with the other components released | run still attempts every component, but the process now exits 1 if any failed |

`git config --unset-all remote.origin.tagOpt` is the one exception left unchecked on purpose: it legitimately returns non-zero when the key was never set (e.g. the clone used `tags: true`), which isn't a failure.

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `PLUGIN_OUTPUT_TAGS_FILE` | `""` | File to write created tags to — one per line. Always created/truncated at startup even if no tags are produced. Consumed by `buildah-master-versions` when building Docker images. |
| `PLUGIN_OUTPUT_LOCATIONS_FILE` | `""` | File to write all accepted locations to — one per line, sorted. Always created/truncated at startup (empty if nothing qualifies). A location appears here only when **both** conditions are met: (1) the line starts with a type defined in `cliff.toml` `commit_parsers` (including `skip=true` types such as `other` and `code_description`) followed immediately by `[`, and (2) the location inside `[]` matches `PLUGIN_CHANGELOG_LEVEL`. Lines that fail either check are silently excluded. Example with `PLUGIN_CHANGELOG_LEVEL=2`: `other[natnat]: msg` is excluded (0 slashes, level expects 1); `other[plugins/natnat]: msg` is included (1 slash, passes level 2). Useful for cross-referencing against actually-changed directories; see [section 8](#8-cross-referencing-with-changed-files). |
| `PLUGIN_SCOPE_EXCLUDE_REGEX` | `""` | Python regex applied to every location before processing. Any matching location is skipped. Example: `^docs$\|^scripts$`. |
| `PLUGIN_VERBOSE` | `0` | `0` = minimal output, `1` = show git-cliff commands, `2` = full trace including stderr. |
| `PLUGIN_INITIAL_TAG` | `1.0.0` | Version used for the first release of a component with no existing tag. |
| `PLUGIN_V_PREFIX` | `"true"` | `"true"` → tags use `v` prefix (`nati-v1.0.0`). Set to `"false"` to disable — `nati-1.0.0`. |
| `PLUGIN_CLIFF_TOML` | *(bundled)* | Path to a custom `cliff.toml`. Resolution order: (1) this variable, (2) `./cliff.toml` in working dir, (3) bundled copy in the image. |

---

## 6. Triggering events — manual, pull_request, and push (merge)

The plugin retrieves its own message — there's no explicit input step. It looks at
`CI_PIPELINE_EVENT` (a Woodpecker-provided variable) and picks one of three retrieval paths.
This section walks through what actually happens on each, end to end, across this repo's two
pipelines (`.woodpecker/pr.yml` and `.woodpecker/publish.yml`).

### `manual` — you trigger a run yourself

You open Woodpecker's UI (or CLI) and manually trigger a pipeline, typing the release message
into the trigger dialog's `MESSAGE` field. `publish.yml`'s `Run release (manual)` step passes it
straight through as `PLUGIN_MESSAGE: "${MESSAGE}"`. `_retrieve_manual_message()` uses it as-is
(no external calls) and — because a mistyped message is the #1 cause of a confusing "nothing
released" run — echoes it back line-numbered, whitespace-marked, between `BEGIN`/`END
PLUGIN_MESSAGE` banners, so you can see exactly what was submitted before wondering why a line
didn't match.

**When to use it:** a hotfix on a branch that never goes through a PR, or any release that
doesn't have a PR description to source from. First-releasing a new component still goes through
a PR like any other change — its description drives the release the same way via the
`pull_request`/`push` path below, nothing special about a first release requires `manual`. There's
no branch restriction on this trigger (`when: - event: manual` in `publish.yml`, no `branch:`
filter), so it can run against whatever branch you're on when you trigger it.

### `pull_request` — every PR open/update (`pr.yml`)

Fires whenever a PR is opened or updated against its target branch. The plugin fetches the PR's
**live** description directly from the Bitbucket Server REST API
(`_retrieve_pull_request_message()`, using `PLUGIN_BITBUCKET_TOKEN` /
`CI_FORGE_URL` / `CI_REPO_OWNER` / `CI_REPO_NAME` / `CI_COMMIT_PULL_REQUEST`) — not whatever the
description said when the PR was first opened.

This run computes what *would* be released and builds the candidate images
(`Build and push plugin images` step in `pr.yml`) — but it **never** pushes changelog commits
or creates tags. Doing so would rewrite the PR's own source branch on every push, which would
both re-trigger the `pull_request` event (Woodpecker does not honor `[skip ci]` on
`pull_request`, unlike `push`) and — because a brand-new component doesn't exist on the target
branch yet — re-release `v1.0.0` and duplicate its changelog entry on every single build. This
event exists purely to preview and validate the release and to produce buildable images; nothing
is persisted until the merge.

### `push` to the main branch (merge) — the only event that persists anything

`publish.yml` also triggers on `push`, but scoped tightly: `branch: main` **and**
`evaluate: 'CI_COMMIT_MESSAGE contains "Merge pull request"'`. This is deliberately not
`pull_request_closed` — that event also fires on PR decline and PR delete, which would silently
push stale changelogs and tags for a PR that never actually merged (see
`INCIDENT_PULL_REQUEST_CLOSED_TRAP.md` for the incident this guards against).

By the time this fires, there is no PR context left — `CI_COMMIT_PULL_REQUEST` isn't set on a
plain push — so the Bitbucket-API path used by `pull_request` isn't available here.
`_retrieve_push_message()` instead reads the merge commit's own body via
`git log -1 --pretty=%B` and takes everything after a `DESCRIPTION` marker line. **This only
works if Bitbucket's merge commit actually contains that marker and the PR description under
it** — which is not what Bitbucket produces by default. That's the required setting below.

Once the message is retrieved, `publish.yml`'s `Run release (merge)` step computes every
version, builds and pushes the real images, and the final `Push changelogs to Git` step commits
`CHANGELOG.md` files and creates the release tags — the only point in either pipeline where
anything is actually persisted back to git.

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

**If this isn't configured:** `_retrieve_push_message()` still runs, finds no `DESCRIPTION`
marker, logs a `WARNING`, and falls back to the full merge commit body — which on Bitbucket's
*default* template is just `Merge pull request #123 from feature-branch`, containing no
`[location]` lines at all. The pipeline "succeeds" and silently releases nothing on every merge.

The template's first line matters beyond `DESCRIPTION` extraction, too: `publish.yml`'s
`evaluate: 'CI_COMMIT_MESSAGE contains "Merge pull request"'` guard depends on it staying
`Merge pull request #...` — changing that opening line means updating the `evaluate:` guard as
well, or `publish.yml` will never fire on a real merge.

---

## 7. Tutorial — set up Bitbucket, add the pipeline, release a hotfix

A practical, copy-and-adapt guide for a repo that wants to *use* `master-versions`. Do the three
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
| `bitbucket_token` | A Bitbucket HTTP access token with read access to this repo |
| `docker_username` / `docker_password` | Credentials for the registry you push built images to |

**Create `.woodpecker/pr.yml`** — runs on every PR, computes candidate versions and builds
images, never touches git:

```yaml
clone:
  git:
    image: woodpeckerci/plugin-git:latest

when:
  - event: pull_request

steps:
  - name: Run release
    image: netanelzucaim123/master-versions:latest
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_BITBUCKET_TOKEN:
        from_secret: bitbucket_token
      PLUGIN_CHANGELOG_LEVEL: "1"   # set to whatever depth(s) your components live at, e.g. "2,3"

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
    image: netanelzucaim123/master-versions:latest
    when:
      - event: manual
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_BITBUCKET_TOKEN:
        from_secret: bitbucket_token
      PLUGIN_MESSAGE: "${MESSAGE}"   # the text you type into Woodpecker's manual-trigger dialog
      PLUGIN_CHANGELOG_LEVEL: "1"

  - name: Run release (merge)
    image: netanelzucaim123/master-versions:latest
    when:
      - event: push
    environment:
      PLUGIN_BASE_PATH: "."
      PLUGIN_OUTPUT_TAGS_FILE: "new_tags.txt"
      PLUGIN_BITBUCKET_TOKEN:
        from_secret: bitbucket_token
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
      - find . -name "CHANGELOG.md" -not -path "./.git/*" | xargs -r git add
      - |
        if [ -n "$${CI_COMMIT_BRANCH}" ]; then
          if ! git diff --cached --quiet; then
            git commit -m "chore(release): update CHANGELOG.md files [skip ci]"
            git push --force-with-lease origin "HEAD:$${CI_COMMIT_BRANCH}"
          fi
          for tag in $(cat new_tags.txt); do git tag -f "$tag"; done
          git push --force --tags origin
        fi
```

> `$${CI_COMMIT_BRANCH}` (double `$`) is required, not a typo — Woodpecker rewrites `${...}` in
> `commands:` itself before the shell runs. A single `$` here silently becomes an empty string.

Swap `PLUGIN_CHANGELOG_LEVEL`, `PLUGIN_REPO`, and the two image names for your own values, then
test: open a throwaway PR (`pr.yml` should compute versions and build images, touching no git
state), then merge it (`publish.yml` should push a changelog commit and a tag to `main`). If the
merge run produces nothing, re-check part A first — a missing/incorrect squash template is the
most common cause.

### C. Release a hotfix

Use this when a bug is found in an **older** shipped version, not `main`'s current one — e.g.
`nati` is at `nati-v2.0.0` on `main`, but the fix is for the still-in-production `nati-v1.0.0`.
The goal is `nati-v1.0.1`, not `nati-v2.0.1`.

1. **Cut the branch from the broken release's tag — not from `main`:**
   ```bash
   git fetch --tags
   git checkout -b hotfix/nati-v1.0.1 nati-v1.0.0
   ```
   Branching from `main` instead would drag `main`'s later history (including `nati-v2.0.0`)
   into the new branch, and the release would bump from `2.0.0` instead of `1.0.0`.
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
4. The pipeline resolves the previous version against this branch's own history — so it correctly
   bumps from `nati-v1.0.0` to `nati-v1.0.1`, builds and pushes the fixed image, and pushes the
   changelog commit and tag to `hotfix/nati-v1.0.1` (never to `main`).
5. **Verify:** `git tag -l 'nati-v*' --sort=-version:refname` should show `nati-v1.0.1` next to
   `nati-v1.0.0`, and the new image should be in your registry.
6. **If the fix should also land on `main`**, open a normal PR from the hotfix branch afterward —
   that's a separate, ordinary release through the usual `pull_request`/merge flow.

---

## 8. Cross-referencing with changed-files

`PLUGIN_OUTPUT_LOCATIONS_FILE` writes every accepted location as a sorted, newline-separated list. Because it captures all qualifying locations — including those whose commit type is `skip=true` in `cliff.toml` (e.g. `other`, `code_description`) — it acts as a full scope manifest of everything the PR author claimed to touch, regardless of whether a release was produced.

The [`changed-files`](../changed-files/) plugin writes the set of directories that actually changed in the push. The [`master-versions-vs-changed-files`](../master-versions-vs-changed-files/) plugin then compares the two and reports mismatches:

- **Changed but not declared** — a directory changed on disk but no `[location]` in the PR body covers it
- **Declared but not changed** — a `[location]` appears in the PR body but no files under it actually changed

> No "fetch PR body" step is needed — `master-versions` retrieves its own message (see [§6 Triggering events](#6-triggering-events--manual-pull_request-and-push-merge)), so it only needs the usual `PLUGIN_BITBUCKET_TOKEN`/`PLUGIN_MESSAGE` depending on the triggering event.

```yaml
steps:
  - name: Get changed dirs
    image: netanelzucaim123/changed-files:latest
    settings:
      output_file: changed_dirs.txt
      output_type: dirs
      folder_depth: 1

  - name: Run release
    image: netanelzucaim123/master-versions:latest
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

Use `master-versions` on its own when you only need versioning and changelogs — no Docker image builds involved.

> No "fetch PR body" step is needed — `master-versions` retrieves its own message (see [§6 Triggering events](#6-triggering-events--manual-pull_request-and-push-merge)): the Bitbucket API for `pull_request` events, `PLUGIN_MESSAGE` for `manual` runs, or the merge commit for a `push`. Clone settings don't matter — see [§5](#5-variables).

```yaml
steps:
  - name: Run release
    image: netanelzucaim123/master-versions:latest
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
      - find . -name "CHANGELOG.md" -not -path "./.git/*" | xargs -r git add
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

When each component has a `Dockerfile`, `buildah-master-versions` reads the tags file produced by `master-versions` and builds + pushes the corresponding Docker image for each tag.

```
master-versions                         buildah-master-versions
──────────────────────────────          ──────────────────────────────────────────
parse retrieved message                 reads new_tags.txt line by line
  → nati-1.1.0                     ──►  nati-1.1.0       → PLUGIN_BASE_PATH/nati/Dockerfile
  → plugins-docker-2.0.0           ──►  plugins-docker-2.0.0 → PLUGIN_BASE_PATH/plugins/docker/Dockerfile
appended to new_tags.txt                builds and pushes each image via buildah
```

```yaml
steps:
  - name: Run release
    image: netanelzucaim123/master-versions:latest
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
      - find . -name "CHANGELOG.md" -not -path "./.git/*" | xargs -r git add
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
→ `nati/CHANGELOG.md` updated, tag `nati-1.1.0`

---

### Nested component — patch bump

```
fix[plugins/docker]: increase read deadline to 30s
```
→ `plugins/docker/CHANGELOG.md` updated, tag `plugins-docker-1.0.1`

---

### Repo root release

```
feat[]: add woodpecker pipeline definition
```
→ `CHANGELOG.md` at root updated, tag `1.0.0`

---

### Multiple components from one line

```
feat[nati, check, base/argo]: centralise JWT validation
```
→ Three independent releases: `nati-1.1.0`, `check-1.1.0`, `base-argo-1.1.0`

---

### Breaking change — two ways to force major

```
breaking[nati]: remove /v1 endpoints
feat[nati]!: replace REST with gRPC interface
```
Both produce a major bump.

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
→ Expands to every subdirectory of `plugins/` and releases each independently.

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
