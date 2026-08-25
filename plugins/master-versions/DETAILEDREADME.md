# master-versions — internals

Technical reference for how `master-versions` works under the hood.

---

## Contents

1. [What is git-cliff?](#1-what-is-git-cliff)
2. [Stateless mode — how this plugin uses git-cliff](#2-stateless-mode--how-this-plugin-uses-git-cliff)
3. [cliff.toml explained](#3-clifftoml-explained)
4. [How git-cliff is called internally](#4-how-git-cliff-is-called-internally)
5. [Clone settings don't matter — how shallow/partial clones are handled](#5-clone-settings-dont-matter--how-shallowpartial-clones-are-handled)

---

## 1. What is git-cliff?

git-cliff is a changelog generator. It reads commit messages, groups them by type, and produces structured `CHANGELOG.md` files. It also calculates the next semantic version by looking at what types of changes are present — a `feat` bumps minor, a `fix` bumps patch, a `breaking` change bumps major.

By default git-cliff reads from the git log. This plugin does **not** use that mode.

---

## 2. Stateless mode — how this plugin uses git-cliff

This plugin bypasses git history entirely. Instead of reading commits from the log, it injects the exact commit string — retrieved internally based on `CI_PIPELINE_EVENT` (see the README's "Message retrieval" section) — directly into git-cliff via `--with-commit`.

The key flags that make this work:

| Flag | Purpose |
|------|---------|
| `--with-commit` | Injects a commit string directly — git history is never read for the current change |
| `--tag-pattern` | Restricts git-cliff to only tags belonging to the current component (e.g. `^nati-[0-9]+\.[0-9]+\.[0-9]+$`) — prevents cross-component tag pollution |
| `--bump --bumped-version` | Asks git-cliff to calculate the next version from the injected commit(s), using the last matching tag as the base |
| `--tag` | Sets the new version label when writing the changelog body |
| `-- HEAD..HEAD` | Passes an empty commit range — git-cliff sees no real history, only the `--with-commit` injections |

**Why stateless?**

The PR body line is the **single source of truth**. The same run always produces the same result regardless of what is or isn't in git history. There is no risk of an unrelated commit in the log accidentally triggering a version bump.

git-cliff still uses the git tag list to find the previous version for the base — but only to answer "what was the last version?" It never reads the commit log for the current change.

---

## 3. cliff.toml explained

```toml
[git]
conventional_commits = false

commit_parsers = [
  { message = "^breaking", group = "🚀 🚀 Breaking Changes" },
  { message = "^feat",     group = "✨ Features" },
  { message = "^fix",      group = "🐛 Bug Fixes" },
  { message = "^other",    group = "📦 other", skip = true },
]

[bump]
custom_major_increment_regex = "^breaking"

[changelog]
trim = false
body = """
...
"""
```

### `[git]` section

| Field | Value | What it does |
|-------|-------|-------------|
| `conventional_commits` | `false` | Disables git-cliff's built-in `type(scope): description` parser. Every commit is treated as a raw string. Bump rules and group assignment come entirely from `commit_parsers` regex matches. |

#### `commit_parsers`

An ordered list — **first match wins**, same as the plugin's own `_match_line`. Each entry defines one commit type.

| Field | Meaning |
|-------|---------|
| `message` | Regex matched against the raw commit string from position 0. The plugin uses these exact same patterns to decide which lines in the PR body are commit lines. |
| `group` | The heading this commit appears under in `CHANGELOG.md`. |
| `skip = true` | Drop the commit entirely — no changelog entry, no version bump. The commit still acts as a line boundary in the PR body. |

**Any commit whose message doesn't match any entry is silently dropped by git-cliff.**

To add a new type, add an entry. Example — add `chore` as a no-op:
```toml
{ message = "^chore", group = "🔧 Chores", skip = true }
```

### `[bump]` section

| Field | What it does |
|-------|-------------|
| `custom_major_increment_regex` | Any commit whose message matches this regex forces a **major** bump. Set to `^breaking` so any line starting with `breaking` always produces a major release, regardless of other rules. |

The standard bump logic (when `custom_major_increment_regex` does not match):
- `feat` → minor bump
- `fix` → patch bump
- `!` after `]` → major bump (handled by git-cliff's `breakage_always_bump_major`)

### `[changelog]` section

| Field | What it does |
|-------|-------------|
| `trim = false` | Preserves leading/trailing whitespace in the rendered output. |
| `body` | Tera template rendered once per release. Produces the `CHANGELOG.md` section. |

Key template variables available inside `body`:

| Variable | Value |
|----------|-------|
| `version` | The new tag string, e.g. `nati-1.6.0`. |
| `timestamp` | Unix timestamp — formatted via `date(format="%Y-%m-%d %H:%M")`. |
| `commits` | List of commit objects, grouped by `group` to produce per-section lists. |
| `commit.message` | The raw commit string injected via `--with-commit`, with newlines replaced for multi-line entries. |

The `get_env(name="CI_REPO_URL", default="")` call in the default template links the version heading to the Gitea browse URL if `CI_REPO_URL` is set in the environment.

---

## 4. How git-cliff is called internally

The plugin calls git-cliff **twice** per component.

### Call 1 — bump calculation (subject line only)

```bash
git cliff \
  --config cliff.toml \
  --tag-pattern '^nati-[0-9]+\.[0-9]+\.[0-9]+$' \
  --bump --bumped-version \
  --with-commit 'feat: add login' \
  -- HEAD..HEAD
```

Output: the next version string, e.g. `nati-1.1.0`.

**Why only the subject line?**
With `conventional_commits = false`, git-cliff applies `custom_major_increment_regex` and bump rules against the commit subject. When a commit has a body attached without a blank-line separator, git-cliff fails to isolate the subject and falls back to a patch bump regardless of what the regex matched. Passing only the first line of each commit (the subject) avoids this — the subject alone is sufficient to determine the bump level.

If the output equals the current latest tag, the component is skipped — no releasable commit.

### Call 2 — changelog generation (full multiline string)

```bash
git cliff \
  --config cliff.toml \
  --tag-pattern '^nati-[0-9]+\.[0-9]+\.[0-9]+$' \
  --tag 'nati-1.1.0' \
  --with-commit 'feat: add login
  Full description here.
  Second line of body.' \
  --prepend nati/CHANGELOG.md \
  -- HEAD..HEAD
```

The full multiline commit string is passed here — the body content is needed for the rendered changelog entry. `--prepend` is used if `CHANGELOG.md` already exists; `--output` is used for the first release.

### Per-component flow summary

```
1. git tag -l 'nati-[0-9]*'           → find latest tag (base version)
2. git cliff --bump --bumped-version   → calculate new version (subject only)
3. git cliff --tag 'nati-1.1.0' ...   → write CHANGELOG.md (full commit body)
4. append 'nati-1.1.0' to output tags file
```

---

## 5. Clone settings don't matter — how shallow/partial clones are handled

Version resolution (`git describe`, and git-cliff's own tag lookup) needs real commit ancestry.
`plugin-git`'s `partial: true` default runs `git fetch --depth=1 --filter=tree:0`, which cuts
history at a shallow boundary — on such a clone `git describe` fails with `fatal: No names found,
cannot describe anything`, because the shallow boundary makes the checked-out commit look like it
has no parents.

`release.py` handles this itself before any tag resolution happens: it checks
`git rev-parse --is-shallow-repository`, and if the workspace is shallow, folds `--unshallow`
into the same fetch that establishes the resolved branch:

```python
fetch_result = run_command(
    f"git {auth_opt}fetch {unshallow_opt}origin {resolve_branch}:refs/remotes/origin/{resolve_branch}"
)
```

Verified directly, not assumed: reproduced a real `--depth=1 --filter=tree:0` clone against a git
server with `uploadpack.allowFilter=true` (so the filter was genuinely honored — confirmed via
`remote.origin.partialclonefilter` and a near-empty initial pack, not silently ignored the way a
local `file://` remote does by default), then ran the exact fetch above. Result: `is_shallow`
flipped to `false`, every commit became reachable again, tags auto-followed, and `git describe`
resolved correctly. The `tree:0` filter itself turned out to be irrelevant to the outcome — `git
describe` only needs commit and tag objects, never tree/blob content.

This is why the clone step's `partial`/`depth`/`tags` settings are non-load-bearing: whatever
state the clone leaves the workspace in, `release.py` repairs it before computing any version.
