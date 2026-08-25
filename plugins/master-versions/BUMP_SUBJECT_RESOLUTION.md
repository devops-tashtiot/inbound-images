# Bump-subject resolution — design notes

## The problem

`release.py` calls git-cliff twice per component: once to compute the next version (`--bump
--bumped-version`), and once to render the changelog (full commit text, via `--tag`). The bump call is
deliberately given only the **subject line** of each commit (`c.splitlines()[0]`), not the full multi-line
string — this exists to work around a real upstream git-cliff bug, tracked at
**https://github.com/orhun/git-cliff/issues/1476** ("`custom_major/minor_increment_regex` ignored for
multiline commits when `conventional_commits = false`"). With this repo's `cliff.toml`
(`conventional_commits = false`), git-cliff applies `custom_major_increment_regex` against what it thinks is
the commit subject — and its own subject/body split is unreliable for a multi-line string that isn't
separated by a blank line (which is exactly how continuation lines in a PR body get joined). Confirmed
directly:

```
git-cliff --with-commit "breaking: major change\nFull description here.\nSecond line of body."
  → nati-v2.0.1   ✗ silently treated as a patch, "breaking" ignored entirely

git-cliff --with-commit "breaking: major change"   (subject only)
  → nati-v3.0.0   ✓ correct — major bump
```

Truncating to the subject line, as `release.py` already did, correctly works around that.

## The new problem this surfaces

A PR body can also be written with the type/location on its own line and the actual description entirely on
the *next* line:

```
feat[nati]:
natiii
```

After `[location]` stripping and continuation-joining, the resulting commit string is `"feat:\nnatiii"` — a
**bare** `"feat:"` on line one, with the real text on line two. Blindly truncating to `splitlines()[0]` here
sends git-cliff just `"feat:"` — a type with *no* description at all — which independently also falls back
to a patch bump, even though `natiii` (the real description) was never even seen:

```
git-cliff --with-commit "feat:\nnatiii"   (full, untruncated)
  → nati-v2.1.0   ✓ correct — git-cliff handles this case fine on its own

git-cliff --with-commit "feat:"           (what plain splitlines()[0] sends)
  → nati-v2.0.1   ✗ wrong — patch, because there's no description left to recognize
```

So the two failure modes need **opposite** handling: a subject that already has real text after the colon
must have any extra lines *dropped* (the original fix); a subject that's bare must have the next line
*folded in* (not dropped) — otherwise there'd be nothing left for git-cliff to correctly identify as `feat`.

## The fix

A small helper, `_bump_subject(commit)`, replaces the plain `c.splitlines()[0]` truncation used only for the
bump call (the changelog call is unaffected — it always gets the full, untouched multi-line string):

```python
def _bump_subject(commit):
    lines = commit.splitlines()
    subject = lines[0]
    if re.search(r':\s*$', subject):
        for line in lines[1:]:
            if line.strip():
                return f"{subject} {line.strip()}"
    return subject
```

- If the first line has real content after the colon (`re.search(r':\s*$', subject)` is `False`) → returned
  unchanged, extra lines dropped. Matches the original, still-needed fix for
  [orhun/git-cliff#1476](https://github.com/orhun/git-cliff/issues/1476).
- If the first line is bare (`type:` or `type!:` with nothing else) → the first non-blank continuation line
  is folded in, synthesizing a normal `"type: description"` subject for the bump call only.

## Examples

| PR body (after `[location]` stripping) | `_bump_subject(...)` result | Bump |
|---|---|---|
| `feat: add login` | `feat: add login` (unchanged) | minor |
| `feat: real subject`\n`Second line of body.` | `feat: real subject` (body dropped) | minor |
| `feat:`\n`natiii` | `feat: natiii` (folded in) | minor |
| `breaking:`\n\n\n`major change` | `breaking: major change` (blank lines skipped over) | major |
| `feat:` (nothing at all, anywhere) | `feat:` (nothing to fold in) | — |

## Files changed

| File | Change |
|---|---|
| `plugins/master-versions/release.py` | Added `_bump_subject(commit)` helper; `bump_commit_args` now uses it instead of a bare `c.splitlines()[0]`. |
| `plugins/master-versions/tests/test_release.py` | New `TestBumpSubject` (unit tests for the helper directly) and a new end-to-end case in `TestHotfixBranchTagResolution` reproducing the `feat[nati]:` + next-line-description scenario against real git-cliff. |

## Reference

Upstream bug this whole subject/body split behavior stems from:
[orhun/git-cliff#1476](https://github.com/orhun/git-cliff/issues/1476) — "`custom_major/minor_increment_regex`
ignored for multiline commits when `conventional_commits = false`." The reporter describes the exact same
root cause and the same "split into two git-cliff invocations" workaround this codebase already uses.
