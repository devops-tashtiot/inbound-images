# The `pull_request_closed` Trap

## The Problem

Our monorepo CI pipeline was running the full validation and changelog generation flow on every pull
request lifecycle event — merge, decline, delete — and attempting to push to `master` on all of them. Only
the merge was supposed to produce a changelog. The declined and deleted PRs were waste at best, and at
worst they corrupted the changelog with stale data.

The cause was a single line in `old/pull_request.yaml`:

```yaml
when:
  - target: master
    event: [pull_request, pull_request_closed]
```

## How It Played Out

In Woodpecker, `pull_request` fires when a PR is opened or updated. `pull_request_closed` fires when a PR
is closed. The problem is that "closed" includes three scenarios:

| Scenario | What happened | Should the pipeline push changelogs? |
|---|---|---|
| PR merged | The PR was merged into `master` | Yes |
| PR declined | The PR was declined (Bitbucket-specific — declined, not merged) | No |
| PR deleted | The PR was closed without merging | No |

With `event: [pull_request, pull_request_closed]`, all four event types hit the pipeline:

- `pull_request` (PR opened/updated) — valid for validation, not for push.
- `pull_request_closed` (PR merged) — should push changelogs.
- `pull_request_closed` (PR declined) — should not push, but the pipeline ran anyway.
- `pull_request_closed` (PR deleted) — should not push, but the pipeline ran anyway.

The git push step was gated with `when: - event: pull_request_closed` to only run on close. But since
`pull_request_closed` also fires on decline and delete, the pipeline would attempt to push changelogs for
PRs that never merged.

### Two classes of damage

**Waste and tag pollution.** On declined and deleted PRs, the pipeline fetched the PR description, ran
`master-versions`, generated changelogs, created new version tags, and pushed both. This burned CI
resources, but the real damage was in the noise it introduced: stale tags for versions that never shipped,
sitting alongside legitimate release tags. When scanning tags to find the latest release, you'd find tags
for features that were rejected. Cleaning them up meant hunting through git history to find which tags
belonged to declined PRs.

**Noise.** Even when the push didn't go through (e.g., the compare step failed because a declined PR's
description was stale), the pipeline run showed up in the CI dashboard as a successful validation run for a
dead PR. This made it harder to track which runs were meaningful.

## The Fix

We separated concerns into two pipelines and replaced the broad `pull_request_closed` trigger with
specific, correct triggers.

### The old approach

One file, two events, a filter at the step level:

```yaml
when:
  - target: master
    event: [pull_request, pull_request_closed] # catches merge, decline, and delete

# ... validation steps run on all events ...

- name: Git - push to repository
  # ...
  when:
    - event: pull_request_closed # fires on merge, decline, and delete
```

The step-level `when` was meant to restrict the push to "when the PR is closed," but "closed" is not the
same as "merged."

### The new approach

Two files, each with a specific trigger:

**`pull_request.yaml`** — validation on open PR, nothing else:

```yaml
when:
  - event: pull_request
    evaluate: 'CI_COMMIT_TARGET_BRANCH == "master"'
```

No `pull_request_closed`. No git push step at all. This pipeline's sole job is to validate that the PR
description matches the changed files. Declined and deleted PRs can trigger a `pull_request` update before
they close, which is fine — the pipeline runs validation and stops.

**`publish_version.yaml`** — changelog generation and push, only on confirmed merges:

```yaml
when:
  - event: push
    branch: master
    evaluate: 'CI_COMMIT_MESSAGE contains "Merge pull request"'
  - event: manual
    branch: ["feat/*", "hotfix/*", "bugfix/*", "fix/*"]
```

This uses `event: push` on `master`, with an additional guard that the commit message contains "Merge pull
request". This is the merge commit created by Bitbucket when a PR is merged. It only fires on an actual
merge.

Declined PRs do not push to `master`, so this trigger never fires. Deleted PRs do not push to `master`, so
this trigger never fires.

The second trigger (`manual` on feature branches) covers the legacy/manual release flow for feature
branches that don't go through a PR to `master`.

## The Result

| Scenario | Old pipeline | New pipeline |
|---|---|---|
| PR opened | Validation runs | Validation runs |
| PR updated | Validation runs | Validation runs |
| PR merged | Validation + push (correct) | Push on master (correct, via separate pipeline) |
| PR declined | Validation + attempt to push (wrong) | No push — no push event on master |
| PR deleted | Validation + attempt to push (wrong) | No push — no push event on master |

## Inside the pipeline

The "retrieve commit message and fetch relevant tags" step extracts the full merge commit:

```bash
MESSAGE=$(git log -1 --pretty=%B)
echo "$MESSAGE" > master_versions_msg.txt
```

This file now contains the entire structured merge commit, including the PR description under the
`DESCRIPTION` header. The `master-versions` plugin reads this file and parses the conventional commit
lines from the PR description, exactly as it did in the old pipeline. The only difference is that the data
now comes from git history rather than the Bitbucket API.

## Why `push` + message evaluation is better than `pull_request_closed`

- **It's semantically correct.** A push to `master` containing "Merge pull request" is the actual merge
  commit. There's no ambiguity about whether the PR was merged, declined, or deleted.
- **It's not tied to pull request semantics.** The pipeline doesn't need to know about PRs at all. It just
  sees a commit on `master` and processes it. This is the same pattern used by the single-app pipeline.
- **It cleanly splits validation from publish.** The `pull_request.yaml` pipeline validates. The
  `publish_version.yaml` pipeline publishes. Each does one thing.

## The Custom Merge Commit Message

The `evaluate: 'CI_COMMIT_MESSAGE contains "Merge pull request"'` guard works because we configured
Bitbucket to use a custom PR merge commit message template. The default Bitbucket merge message only
contains the line `Merge pull request #123 from feature-branch`, which gives us no way to extract the PR
body after the merge.

We changed the Bitbucket merge commit message template to:

```
Merge pull request #${id} from ${fromRefName}

METADATA
Title: ${title}
Target: ${toRepoSlug} (${toRefName})
Source: ${fromRepoSlug} (${fromRefName})

DESCRIPTION
${description}
```

And we also changed the max commit summaries to `0` under the "Commit summaries" section, to not introduce
unnecessary bloat to the merge commit message.

This serves two purposes:

1. **Recognizing a PR merge commit.** The first line always starts with `Merge pull request`, which is what
   the `evaluate` guard checks. Any direct push to `master` (e.g., a hotfix pushed by a developer) won't
   match this pattern, so the pipeline won't fire on it. Only actual PR merges trigger the pipeline.
2. **Extracting the PR body on push events.** In the old approach, the pipeline ran on the
   `pull_request_closed` event and could access the PR description directly via the Woodpecker `pr-msg`
   plugin. But with the new approach, the pipeline only sees a `push` event on `master` — there's no PR
   context available. The merge commit body contains the `${description}` variable, which Bitbucket
   populates with the full PR description. The pipeline retrieves this via `git log -1 --pretty=%B`, getting
   the entire commit message including the `DESCRIPTION` section with the PR body.

## Takeaways

- `pull_request_closed` does not mean "merged." It means the PR is closed, which could be a decline, a
  delete, or a merge. If you need "on merge," use a `push` event on the target branch.
- Add a message-level guard. Evaluating `'CI_COMMIT_MESSAGE contains "Merge pull request"'` ensures the push
  is actually a merge commit, not a direct push to master.
- Separate validation and publish pipelines. A validation pipeline should never have the ability to push.
  This eliminates the entire class of "push when you shouldn't" bugs.
