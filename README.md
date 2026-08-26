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
hand-written. But you never have to figure out *how* your particular base image trusts
a CA — no investigating whether it's Debian, RHEL, Alpine, or something with no trust
store at all. Every Dockerfile in this repo uses the **same three lines**, copied
verbatim, regardless of base image:

```dockerfile
FROM <image>

COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

# ...whatever your component actually needs, after this
```

`scripts/inject-ca.sh` (a local copy lives alongside every component's own `Dockerfile`
— see "Layout" below) auto-detects which of four mechanisms your base image actually
needs and does it: `update-ca-trust` (RHEL/UBI, including `ubi9-minimal`, which has it
despite the name), `update-ca-certificates` (Debian/Ubuntu), append-to-bundle (Alpine —
no `update-ca-certificates` package present, no network needed), or a plain file `COPY`
into `SSL_CERT_DIR` for an image with no OS trust-store tooling at all (the two
`gcr.io/kaniko-project/executor` plugins have no `update-ca-*` binary, no
`/etc/os-release` — Go's `crypto/x509` reads `SSL_CERT_DIR` directly, so dropping the
file in is the entire fix). Verified against every distinct base-image family actually
in use here, real builds, real CA-landed checks each time — including the SSL_CERT_DIR
case, which is new: the two kaniko plugins previously `COPY`d straight into
`/kaniko/ssl/certs/` themselves, hand-picking that mechanism; now they use the exact
same three lines as everything else, and the generic script picks it for them.

**This is enforced in CI, not just documented** — `check-ca` runs
`scripts/check-ca-injection.sh` right after `version`, and **fails the build** if any
component targeted this run:

- has no real `COPY` of the cert, no real `COPY` of `inject-ca.sh`, or `COPY`s the
  injector but never `RUN`s it. Checked against actual instruction lines with comments
  stripped first — a Dockerfile that only *mentions* either file in a comment doesn't
  count (verified: constructed exactly that case, confirmed it fails).
- *does* have all three lines, but from a local `cloudflare-origin-ca-rsa-root.pem`
  and/or `inject-ca.sh` copy that's gone stale — no longer byte-matches the real ones
  at `certs/cloudflare-origin-ca-rsa-root.pem` / `scripts/inject-ca.sh`. This is the
  failure mode that actually matters for a rollout, or for a fix/new distro case added
  to the canonical injector: `[**]` bumping a component's version number doesn't help
  if the Dockerfile it rebuilds from is still using last year's cert or last month's
  injector logic.

**No exemption mechanism exists** — a speculative "this one doesn't need it" escape
hatch was considered and dropped: nothing in this repo has ever actually needed one,
and an unused exemption is a silent, unreviewed way for a real gap to slip through
later. If a genuine no-CA-possible case ever shows up, add the check for it then,
against the real constraint.

**Scoped to what this run actually targeted, not every component in the repo.**
`check-ca` reads `new_locations.txt` — the file `version` (running just before it)
already writes via `PLUGIN_OUTPUT_LOCATIONS_FILE`, listing exactly which component(s)
the message resolved to. That's real, already-implemented location-resolution logic
(the same wildcard/indicator-file rules from "How `[**]` finds components" above) —
`check-ca` doesn't reimplement any of it, just reads the result. So
`fix[plugins/master-versions]: ...` checks only `plugins/master-versions`; a
`breaking[**]: ...` rotation resolves to every component, so it still checks all of
them, same as before. Verified: a targeted single-location commit produces a
one-line `new_locations.txt` and a scoped, single-component check run; a `[**]`
commit produces all 15 lines and checks all 15; a run with nothing releasable leaves
`new_locations.txt` empty and the check passes immediately with nothing to do. A PR
touching one component is judged on that component's own CA state — not blocked by
an unrelated, pre-existing problem elsewhere in the repo it doesn't even target.

`check-ca` runs after `version` but before `build-and-push` — Woodpecker stops the
pipeline on a failed step, so a bad CA still blocks the actual build/push even though
`version` already ran; `version`'s local `VERSION.txt`/`CHANGELOG.md` writes just
never get committed, since `commit-back` (the last step) never runs either.

All 15 components currently pass.

### If the cert file (or the injector script) is ever renamed

`scripts/check-ca-injection.sh` reads both filenames from environment variables —
`PLUGIN_CA_CERT_FILENAME` (default `cloudflare-origin-ca-rsa-root.pem`) and
`PLUGIN_CA_INJECTOR_FILENAME` (default `inject-ca.sh`), both set in
`.woodpecker/build.yaml`'s `check-ca` step. Those two variables are the only place
*this script* needs to change on either rename; verified live for the cert filename
(pointed it at a name that doesn't exist, confirmed the check correctly fails with a
clear "does not exist" message instead of silently passing against the old file) —
the injector filename check works identically, same code path.

Two things still need editing by hand regardless, and can't be made one-place — not
an oversight, a real Woodpecker/Dockerfile constraint:
- **Every component's own `Dockerfile`** — its `COPY` line names the file directly, so
  a rename means updating all 15 (or however many exist by then) by hand, same as
  updating their cert content on a rotation.
- Nothing in `outbound-images-with-ca`'s `.woodpecker/build.yaml` needs this (it has no
  equivalent check script), but that repo's own `when.path` gate entries are similarly
  static YAML — see its README for the same caveat.

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
│   ├── inject-ca.sh                  # the canonical generic injector every component's own copy must match
│   └── check-ca-injection.sh         # CI gate: every Dockerfile injects the CA, no stale copies
├── base/<name>/{Dockerfile, cloudflare-origin-ca-rsa-root.pem, inject-ca.sh, VERSION.txt, CHANGELOG.md}
├── plugins/<name>/{Dockerfile, cloudflare-origin-ca-rsa-root.pem, inject-ca.sh, VERSION.txt, CHANGELOG.md, ...}
└── .woodpecker/build.yaml
```

`VERSION.txt`/`CHANGELOG.md` per component are written by `version-file`, not hand-edited —
a fresh component just needs its `Dockerfile`; the first commit that targets it seeds both.
