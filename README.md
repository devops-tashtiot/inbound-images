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
component built from an unusual base. **Every one of the 15 components currently in this
repo injects the Cloudflare Origin CA explicitly in its own Dockerfile** — verified, not
assumed:

```
for f in base/*/Dockerfile plugins/*/Dockerfile; do
  grep -qi "cloudflare-origin-ca\|update-ca-certificates\|update-ca-trust" "$f" \
    && echo "ok:   $f" || echo "MISSING: $f"
done
```

Run that before approving any PR that adds a new component — a Dockerfile that doesn't
show up here can't be trusted to talk to `*.devopstashtiot.page` correctly, regardless of
what its `VERSION.txt`/`CHANGELOG.md` say. The exact injection technique differs by base
image (this repo currently has three): `update-ca-trust` for the RHEL/UBI family
(including `ubi9-minimal`, which has it despite the name), append-to-bundle for Alpine
(no `update-ca-certificates` package present, and no network needed), and for the two
`gcr.io/kaniko-project/executor` plugins — which have **no** OS trust tooling at all, not
even `/etc/os-release` — a plain `COPY` into `/kaniko/ssl/certs/` (the directory
`SSL_CERT_DIR` already points at; Go's `crypto/x509` reads every file in it directly, no
"update" step exists or is needed).

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
   directory anywhere under `PLUGIN_BASE_PATH` that already has its own `VERSION.txt`" —
   which, in this repo, is all 15 components at once. `breaking` forces a major bump on
   every one of them, same as any other `breaking` commit.
4. Open a PR as normal. Verified locally: a single `breaking[**]: ...` message correctly
   produced 15 tags (`base-uv-python-38-v2.0.0` through `plugins-version-file-v2.0.0`),
   one per component, all in the same run.

Unlike `outbound-images-with-ca` (where the CA rotation and the image rebuild are two
separate, automatic pipeline steps), here the rotation *is* just an ordinary `[**]`-target
commit — there's no separate "detect the cert changed" trigger, because `version-file`
doesn't need one: naming `**` in the message is already enough to reach everything.

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
