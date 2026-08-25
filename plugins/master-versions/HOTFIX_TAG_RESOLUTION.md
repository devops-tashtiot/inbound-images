# Branch-correct tag resolution — design notes

## The problem

`release.py` finds "the latest tag" for a component with:

```python
git tag -l '{tag_glob}' --sort=-version:refname
```

This picks the **globally highest semver tag** matching the component's glob (e.g. `nati-v[0-9]*`) — with
zero regard for git ancestry or which branch is actually being built. git-cliff's own
`--bump --bumped-version` call does the exact same thing independently: it scans every local tag ref
matching `--tag-pattern` and takes the max.

That breaks the moment a component has more than one active line of history. Concretely: a component has
`nati-v1.0.0` and `nati-v2.0.0` on `master`. A hotfix branch is cut from `nati-v1.0.0` to fix a bug found in
that release. The fix should be tagged `nati-v1.0.1`. Instead, both the script and git-cliff resolve against
`nati-v2.0.0` — the highest tag *anywhere in the repo* — and produce `nati-v2.0.1`, a version that has
nothing to do with the branch actually being released.

## Solutions considered, and why each was rejected

### 1. Ancestry-aware lookup: `git describe` + git-cliff's `--use-branch-tags`

`git describe --tags --abbrev=0 --match '<glob>' <ref>` correctly walks commit ancestry and finds the
nearest tag reachable from a given ref — verified directly against a real repo, giving `1.0.0` on the
hotfix branch and `2.0.0` on `master`, as expected. git-cliff has a matching flag, `--use-branch-tags`
("include only the tags that belong to the current branch"), which made its own internal bump search
ancestry-aware too.

**Rejected for two independent reasons, both confirmed empirically:**

- **Cost.** Ancestry-aware resolution needs real commit history. A shallow (`depth: 1`) clone severs the
  parent link at the fetch boundary — even with every tag ref present locally, `git describe` failed
  outright ("no tags can describe..."), because the shallow boundary marks the checked-out commit as having
  no parents at all, regardless of what other objects happen to be present. Fixing that requires either a
  full `depth: 0` clone (this repo's `.git` is 451 MB — a real, recurring cost paid on *every* pipeline run)
  or a bounded `--deepen`/`--unshallow` retry loop (extra moving parts).
- **`--use-branch-tags` can't be redirected.** It only ever looks at whatever is *actually checked out*
  (`HEAD`). For a PR build, the commit under test is the PR's own branch — not the target/base branch we'd
  want the ancestry check to run against. Pointing the tool's commit-range argument at a different ref
  (e.g. `master..master` instead of `HEAD..HEAD`) does **not** change which branch `--use-branch-tags`
  considers "current" — verified directly: it still resolved against the checked-out branch's own ancestry
  regardless of the range argument. Making it look at a different branch would require an actual
  `git checkout` of that branch mid-script — a real, stateful change to the working tree that risks
  interfering with later pipeline steps.

### 2. Narrow the tag pattern itself (declared release line, or an exact-match pattern)

Idea: instead of teaching anything about ancestry, just make the `--tag-pattern` regex itself only match
the relevant version line — e.g. `^nati-v1\.0\.[0-9]+$` instead of `^nati-v[0-9]+\.[0-9]+\.[0-9]+$`. Cheap,
no ancestry needed, no clone-depth dependency.

**Rejected — fundamentally broken for `breaking` commits.** git-cliff validates that the *computed next
version* also matches `--tag-pattern`. A commit that needs to cross the narrowed pattern's boundary (e.g.
`breaking[nati]: ...` bumping `1.0.0 → 2.0.0`) fails outright with a git-cliff error
(`Next version (nati-v2.0.0) does not match the tag pattern: ^nati-v1\.0\.[0-9]+$`), regardless of how the
narrowed pattern was derived (a manually declared release line, or an exact-match pattern built from a
resolved tag). This isn't a one-off bug in one variant — it affects the entire family of "shrink the regex"
approaches, since the conflict is structural: the same pattern is used both to *find* the base tag and to
*validate* the result, and those two jobs need different amounts of permissiveness.

### 3. Blanket `--tags` fetch, then temporarily delete/restore the "wrong" tag around the git-cliff calls

Fetch everything (as today), but before invoking git-cliff for a given component, capture and delete any
tag matching the component's glob except the one we've independently determined (via `git describe`) is
correct — restoring it afterward.

**Rejected as unnecessary complexity**, once the next approach showed the same result is achievable without
ever fetching the wrong tag in the first place — nothing to delete if it's never there.

### 4. Selectively fetch only "reachable" tags via `git ls-remote` + `git cat-file -e`

Ask the remote for tag name→SHA pairs (`git ls-remote --tags`, no object transfer), then for each, check
whether that commit is already present locally (`git cat-file -e <sha>`) as a result of a targeted branch
fetch — only create a local tag ref for the ones that pass. This correctly avoided ever materializing the
wrong tag, and worked in testing.

**Set aside because of the added network round-trip and bespoke reconciliation logic** — the user
specifically flagged the extra `ls-remote` call as an unwanted cost/complexity, which led to re-examining
whether git already does this same "is this tag's commit part of what I just fetched" check natively.

### 5. `git rev-list --tags --max-count=1`

Idea: instead of `git describe`, find the most recently-committed tagged commit and describe from there.

**Rejected — answers a different question than the one that matters, and isn't reliably scoped.**
`git rev-list --tags` considers every tag in the repo, not just the current branch's ancestry; a real test
showed it picking a commit from a completely unrelated line of history simply because it had the latest
commit timestamp, repo-wide. Even after adding `--tags='<pattern>'` scoping (which does fix cross-component
contamination) and testing only tags belonging to one component, it still failed in a case where a tag sat
**directly on the commit being described** — `rev-list` picked an older, unrelated tag instead, because it
orders by commit date, not graph distance. `git describe` doesn't have this failure mode: it never looks at
timestamps, only at parent/child edges.

## The solution that shipped

Two things, working together:

**1. Resolve which branch's tags to trust, per pipeline run** (`CI_PIPELINE_EVENT`, `CI_COMMIT_BRANCH`,
`CI_COMMIT_TARGET_BRANCH` — all read automatically, never user-set):

- Pull-request event → the **target** branch (`CI_COMMIT_TARGET_BRANCH`, e.g. `main`) — "what would this
  look like once merged." Confirmed directly from Woodpecker's Gitea-forge source
  (`pipeline.Branch = hook.PullRequest.Base.Ref`) that `CI_COMMIT_BRANCH` equals the target branch for PR
  events, *not* the PR's own source branch — so the target must be read explicitly via
  `CI_COMMIT_TARGET_BRANCH`.
- Otherwise (direct push, manual trigger) → `CI_COMMIT_BRANCH` itself — the actual hotfix-branch case.
- Falls back to `HEAD` if neither variable is set (e.g. a bare local run outside Woodpecker).

**2. Fetch that branch into an explicit ref, with no `--tags`/`--no-tags` flag on the fetch itself,** and let
git's built-in default ("auto-follow") behavior decide which tags attach:

```python
run_command("git config --unset-all remote.origin.tagOpt")
...
run_command(f"git fetch origin {resolve_branch}:refs/remotes/origin/{resolve_branch}")
```

Git's auto-follow rule: a tag is attached **only if** the commit it points to is already present locally as
a *result of this fetch*. It never reaches out for a commit just because a tag happens to be named after
it — that's what distinguishes it from `--tags` (fetch every tag, downloading whatever's needed) and
`--no-tags` (fetch none, even for commits already present). Structurally, this means a tag from an unrelated
branch can never attach, without any custom filtering code at all.

Two details that took real testing to get right, not just reasoning:

- **The clone step's own `tags:` setting can silently defeat this.** Woodpecker's `plugin-git` runs
  `git fetch --tags` (or `--no-tags`) as a single combined command with the ref/depth — confirmed by
  reading its source directly, not assumed. With the *previous* `tags: true` setting, the clone step itself
  pulls in every tag before `release.py` ever runs, and nothing downstream removes an already-present tag.
  So `tags: false` is required in `.woodpecker/Manual_param.yaml` / `Manual.yaml`, and `release.py` resets
  `remote.origin.tagOpt` defensively at startup, since `tags: false` persists as `--no-tags` on the remote
  and would otherwise also block the script's *own* later fetch.
- **Having the commit data locally isn't enough on its own — the fetch has to actually happen.** The first
  design assumed that with a full (`depth: 0`) clone, the branch already being checked out wouldn't need any
  further fetch at all. Testing showed that's wrong: tag auto-follow only fires during a real fetch
  negotiation, and a plain re-fetch of a branch git already tracks is treated as a no-op that skips
  auto-follow — even though the commit data (and the tag's target) was already present. What works,
  verified directly: fetching into an **explicit** `refs/remotes/origin/<branch>` destination forces a real
  negotiation, which correctly triggers auto-follow, whether the branch is a brand-new ref (the PR/target
  case) or the one already checked out (the direct-branch case). Both cases are therefore unified into the
  same fetch call.

`depth: 0` was kept (a deliberate, accepted recurring cost — this repo's `.git` is 451 MB) in exchange for
not needing any shallow-repository special-casing in the code.

## Why `breaking` commits still work correctly

Every rejected pattern-narrowing approach broke on major-version-crossing commits because it restricted
`--tag-pattern` by version number. The shipped solution never touches `--tag-pattern` at all — it stays the
original, fully unrestricted regex (`^nati-v[0-9]+\.[0-9]+\.[0-9]+$`) in both the bump call and the
changelog call. The only thing being controlled is **which tag refs physically exist on disk**, which is an
orthogonal concern to what the regex will accept — so a `breaking` commit crossing from `1.0.0` to `2.0.0`
still validates fine, confirmed directly for both the hotfix-branch and PR cases.

## Examples — what actually gets generated

All of these assume the same starting state: component `nati` has `nati-v1.0.0` tagged on `master`, `master`
later moved on and was also tagged `nati-v2.0.0`, and a `hotfix` branch was cut from the `nati-v1.0.0`
commit — so `hotfix` and `nati-v2.0.0` share no history.

### Example 1 — Direct/manual build of the hotfix branch (patch fix)

```
CI_COMMIT_BRANCH=hotfix
PLUGIN_MESSAGE: fix[nati]: patch bug found in 1.0.0
```

| Step | Value |
|---|---|
| `resolve_branch` | `hotfix` (no PR event, so `CI_COMMIT_BRANCH` is used as-is) |
| Tags visible after fetch | `nati-v1.0.0` only — `nati-v2.0.0`'s commit was never fetched |
| `git describe` result | `nati-v1.0.0` |
| git-cliff bump | patch |
| **Resulting tag** | **`nati-v1.0.1`** |

### Example 2 — Same hotfix branch, opened as a pull request into `master`

```
CI_PIPELINE_EVENT=pull_request
CI_COMMIT_TARGET_BRANCH=master
PLUGIN_MESSAGE: feat[nati]: small addition
```

| Step | Value |
|---|---|
| `resolve_branch` | `master` (PR event, so the target branch is used, not the PR's own branch) |
| Tags visible after fetch | `nati-v1.0.0` **and** `nati-v2.0.0` — both are genuinely part of `master`'s own history |
| `git describe` result | `nati-v2.0.0` (master's real latest) |
| git-cliff bump | minor |
| **Resulting tag** | **`nati-v2.1.0`** |

### Example 3 — Breaking change committed on the hotfix branch

```
CI_COMMIT_BRANCH=hotfix
PLUGIN_MESSAGE: breaking[nati]: major change
```

| Step | Value |
|---|---|
| `resolve_branch` | `hotfix` |
| Tags visible after fetch | `nati-v1.0.0` only |
| `git describe` result | `nati-v1.0.0` |
| git-cliff bump | major (crosses the version boundary — `--tag-pattern` was never narrowed, so this validates fine) |
| **Resulting tag** | **`nati-v2.0.0`** |

### Example 4 — What the *old* logic would have produced for the same three cases

| Case | Old result | New result |
|---|---|---|
| Hotfix fix commit | `nati-v2.0.1` (wrong — bumped from master's tag) | `nati-v1.0.1` |
| PR feature commit into master | `nati-v2.1.0` (right, but only by coincidence — same "highest tag wins" logic) | `nati-v2.1.0` |
| Hotfix breaking commit | `nati-v3.0.0` (wrong — major-bumped from master's `2.0.0`, not hotfix's own `1.0.0`) | `nati-v2.0.0` |

## Files changed

| File | Change |
|---|---|
| `plugins/master-versions/release.py` | Reset `remote.origin.tagOpt`; resolve `CI_PIPELINE_EVENT`/`CI_COMMIT_BRANCH`/`CI_COMMIT_TARGET_BRANCH` into a branch to trust; fetch it into `refs/remotes/origin/<branch>`; tag lookup switched from `git tag -l --sort=-version:refname` to `git describe --tags --abbrev=0 --match '<glob>' <resolved_ref>`. |
| `.woodpecker/Manual_param.yaml`, `.woodpecker/Manual.yaml` | Clone settings: `tags: true` → `tags: false`, `depth` → `0`. |
| `plugins/master-versions/tests/test_release.py` | New `TestBranchResolution` (mocked) covering PR/non-PR branch selection and the `tagOpt` reset; new `TestHotfixBranchTagResolution` (real git + real git-cliff, not mocked) reproducing the hotfix, PR, and breaking-commit scenarios end-to-end. |
