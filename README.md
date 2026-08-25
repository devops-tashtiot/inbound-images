# builtin-images

The org's own base images and Woodpecker plugins — real, differing content per image, not a
thin wrapper over an upstream. Versioned independently per component by commit message,
exactly like `master-versions` already works in `a-woodpecker-plugins` — same grammar, same
rules, just backed by a per-component `VERSION.txt` instead of a git tag lookup. Upstream
images that only need the internal CA trusted live in the sibling repo `cicd-images`
instead — those have no content of their own to version independently.

## Commit message format

```
feat[base/uv-python-311]: bump pinned uv to 0.12.0
fix[plugins/sonarqube]: correct scanner CLI version pin
feat[plugins/master-versions]!: drop legacy shallow-clone self-heal
```

Identical grammar to `master-versions` (`type[location]: description`, multi-location,
`!` forces major) — see [`scripts/version-file/README.md`](scripts/version-file/README.md)
for the exact rules (wildcards, the three version-resolution cases, etc).

## `scripts/version-file/` — the orchestrator, vendored not pulled

`version-file`'s own source lives directly in this repo, the same "not a published
dependency" pattern `cicd-images` uses for `fan_out.py`. It reads each targeted
component's own `VERSION.txt` (not a git tag) to find its current version, bumps it via
git-cliff, and writes both `VERSION.txt` and `CHANGELOG.md` back.

## Layout

```
builtin-images/
├── cliff.toml                      # feat/fix/breaking — the a-woodpecker-plugins standard
├── scripts/version-file/           # vendored orchestrator (release.py, cliff.toml, tests)
├── base/<name>/{Dockerfile, VERSION.txt, CHANGELOG.md}
├── plugins/<name>/{Dockerfile, VERSION.txt, CHANGELOG.md, ...its own real content}
└── .woodpecker/build.yaml
```

`VERSION.txt`/`CHANGELOG.md` per component are written by `version-file`, not hand-edited —
a fresh component just needs its `Dockerfile`; the first commit that targets it seeds both.
