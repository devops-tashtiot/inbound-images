# inbound-images

The org's own base images and Woodpecker plugins — real, differing content per image, not a
thin wrapper over an upstream. Versioned independently per component by commit message,
exactly like `master-versions` already works in `a-woodpecker-plugins` — same grammar, same
rules, just backed by a per-component `VERSION.txt` instead of a git tag lookup. Upstream
images that only need the internal CA trusted live in the sibling repo `outbound-images-with-ca`
instead — those have no content of their own to version independently.

## Which repo does my image belong in?

One question decides it — everything else follows from the answer.

```
                    Are you writing this image's
                          content yourself?
                                 │
                 ┌───────────────┴────────────────┐
                yes                                no
      (app code, packages,                 (existing upstream
           scripts)                              image)
                 │                                 │
                 ▼                                 ▼
      ┌───────────────────────┐        ┌──────────────────────────┐
      │    inbound-images      │        │  outbound-images-with-ca │
      │    (this repo)          │        │                          │
      │ you write the           │        │ automation writes the    │
      │ Dockerfile + CA block   │        │ Dockerfile + CA block    │
      └────────────┬─────────────┘        └─────────────┬────────────┘
                   │                                     │
                   ▼                                     ▼
        write Dockerfile +                  push image to outbound_images
      feat[base/name]: commit                  + one line in images.txt
```

*(plain text, not Mermaid — Bitbucket Data Center doesn't render Mermaid fences in README.md, so this is kept as a fixed-width diagram that displays identically everywhere.)*

| Question | `inbound-images` (this repo) | `outbound-images-with-ca` |
|---|---|---|
| Who writes the Dockerfile? | you | generated automatically (hand-written escape hatch for scratch/distroless) |
| Who injects the CA? | you, in your own Dockerfile | generated automatically |
| What decides the version? | your commit message (`feat`/`fix`/`!`) | the CA's own version — nothing else |
| What do you edit to publish? | a new folder + Dockerfile | one line in `images.txt` |

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
dependency" pattern `outbound-images-with-ca` uses for `fan_out.py`. It reads each targeted
component's own `VERSION.txt` (not a git tag) to find its current version, bumps it via
git-cliff, and writes both `VERSION.txt` and `CHANGELOG.md` back.

## Layout

```
inbound-images/
├── cliff.toml                      # feat/fix/breaking — the a-woodpecker-plugins standard
├── scripts/version-file/           # vendored orchestrator (release.py, cliff.toml, tests)
├── base/<name>/{Dockerfile, VERSION.txt, CHANGELOG.md}
├── plugins/<name>/{Dockerfile, VERSION.txt, CHANGELOG.md, ...its own real content}
└── .woodpecker/build.yaml
```

`VERSION.txt`/`CHANGELOG.md` per component are written by `version-file`, not hand-edited —
a fresh component just needs its `Dockerfile`; the first commit that targets it seeds both.
