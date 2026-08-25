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

## Every image here trusts the CA — no exceptions

Unlike `outbound-images-with-ca`, nothing generates these Dockerfiles — each one is
hand-written, so CA injection has to be added by hand too, and it's easy to forget on a
component built from an unusual base. **This is enforced in CI, not just documented** —
`.woodpecker/build.yaml`'s `check-ca` step runs `scripts/check-ca-injection.sh` before
anything else, and **fails the build** if any component:

- has no CA injection at all. **This is a real check, not a loose grep** — it strips
  comments first, then requires one of a fixed set of genuine patterns to appear on an
  actual `RUN`/`COPY` instruction: `update-ca-trust`, `update-ca-certificates`, a
  `COPY` into a known OS trust-store path, or a `RUN` appending into the known Alpine
  bundle. A Dockerfile that only *mentions* the cert — in a comment, or `COPY`d
  somewhere inert like `/tmp/` — now fails instead of incidentally passing (verified:
  both of those exact cases were constructed and confirmed to fail). **No exemption
  mechanism exists** — a speculative "this one doesn't need it" escape hatch was
  considered and dropped: nothing in this repo has ever actually needed one (not even
  the two `kaniko-*` plugins, which have no OS trust store at all — no `update-ca-*`
  binary, no `/etc/os-release` — and still inject the CA correctly, see below), and an
  unused exemption is a silent, unreviewed way for a real gap to slip through later. If
  a genuine no-CA-possible case ever shows up, add the check for it then, against the
  real constraint.
- *does* inject a CA, but from a local copy that's gone stale — i.e. its own
  `<component>/cloudflare-origin-ca-rsa-root.pem` no longer byte-matches the real
  `certs/cloudflare-origin-ca-rsa-root.pem`. This is the failure mode that actually
  matters for a rollout: `[**]` bumping a component's version number doesn't help if
  the Dockerfile it rebuilds from is still `COPY`ing last year's cert.

All 15 components currently pass. The exact injection technique differs by base image
(this repo currently has three): `update-ca-trust` for the RHEL/UBI family (including
`ubi9-minimal`, which has it despite the name), append-to-bundle for Alpine (no
`update-ca-certificates` package present, and no network needed), and for the two
`gcr.io/kaniko-project/executor` plugins — a plain `COPY` into `/kaniko/ssl/certs/` (the
directory `SSL_CERT_DIR` already points at; Go's `crypto/x509` reads every file in it
directly, no "update" step exists or is needed).

## How to rotate the CA for every image here (not just one)

An ordinary commit message (`feat[base/uv-python-311]: ...`) only ever bumps the one
location you name — exactly right for a normal change, wrong for a CA rotation, which
needs to touch all 15 components at once, the same way `outbound-images-with-ca` rebuilds
every declared image on a rotation.

1. Replace `certs/cloudflare-origin-ca-rsa-root.pem` with the new certificate.
2. Update the same cert file inside **every** component folder that keeps its own copy
   (each Dockerfile `COPY`s a local copy, not the repo-root one — see each folder's own
   `cloudflare-origin-ca-rsa-root.pem`).
3. Commit with `breaking[**]: <why>` — the `**` wildcard (already implemented in
   `scripts/version-file/release.py`, not something built for this) means "every
   directory anywhere under `PLUGIN_BASE_PATH` that has its own
   `PLUGIN_COMPONENT_INDICATOR_FILE`". `breaking` forces a major bump on every one of
   them, same as any other `breaking` commit.
4. Open a PR as normal. Verified locally: a single `breaking[**]: ...` message correctly
   produced 15 tags (`base-uv-python-38-v2.0.0` through `plugins-version-file-v2.0.0`),
   one per component, all in the same run.

Unlike `outbound-images-with-ca` (where the CA rotation and the image rebuild are two
separate, automatic pipeline steps), here the rotation *is* just an ordinary `[**]`-target
commit — there's no separate "detect the cert changed" trigger, because `version-file`
doesn't need one: naming `**` in the message is already enough to reach everything.

### How `[**]` (and `[*]`) find components: `PLUGIN_COMPONENT_INDICATOR_FILE`

`_all_versioned_dirs` (what `[**]`/`[base/**]` expand to, and — see below — what
`[*]`/`[base/*]` filter by too) doesn't scan for components directly; it treats
whatever file `PLUGIN_COMPONENT_INDICATOR_FILE` names as the signal "this directory is
a real component." Two real options, with a real tradeoff:

- **`VERSION.txt`** (the library default) — only exists *after* a component's first
  release. Simple, but means `[**]` **cannot reach a component's own first release** —
  confirmed live: a fresh folder with only a `Dockerfile` and no `VERSION.txt` yet,
  targeted with `breaking[**]: ...`, produced `"No components to release after
  expansion/filtering"` — a **silent no-op**, not an error.
- **`Dockerfile`** (what this repo actually sets, in `.woodpecker/build.yaml`'s
  `version` step) — exists from the moment a component is created, so `[**]` reaches it
  on its true first release too. Also verified live: the same fresh-folder case, rerun
  with `PLUGIN_COMPONENT_INDICATOR_FILE=Dockerfile`, correctly found it, computed
  `1.0.0`, and wrote `VERSION.txt` for the first time — no separate by-name commit
  needed first.

This applies to `[*]`/`[base/*]` too, not just `[**]` — a direct subdir *without* the
indicator file (a `docs/` or `scripts/` folder sitting next to real components, say) is
silently excluded from the expansion, the same filtering `**` already applied. Also
verified live: `[plugins/*]` against a folder with a `Dockerfile` and a sibling folder
with only a `README.md` correctly targeted just the former.

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
├── certs/cloudflare-origin-ca-rsa-root.pem   # the canonical cert every component's own copy must match
├── scripts/
│   ├── version-file/                 # vendored orchestrator (release.py, cliff.toml, tests)
│   └── check-ca-injection.sh         # CI gate: every Dockerfile injects the CA, no stale copies
├── base/<name>/{Dockerfile, cloudflare-origin-ca-rsa-root.pem, VERSION.txt, CHANGELOG.md}
├── plugins/<name>/{Dockerfile, cloudflare-origin-ca-rsa-root.pem, VERSION.txt, CHANGELOG.md, ...}
└── .woodpecker/build.yaml
```

`VERSION.txt`/`CHANGELOG.md` per component are written by `version-file`, not hand-edited —
a fresh component just needs its `Dockerfile`; the first commit that targets it seeds both.
