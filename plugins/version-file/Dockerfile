FROM docker.io/netanelzucaim123/python-git-cliff:latest

WORKDIR /app

# Build context is this folder itself — buildah-master-versions builds every
# component with its own folder as context, not the repo root.
COPY release.py /app/release.py
COPY cliff.toml /app/cliff.toml

# release.py fetches the PR body over HTTPS from Bitbucket, which is reached
# internally via ingress-nginx presenting a Cloudflare Origin CA-issued cert
# (not publicly trusted). Trust that root so urllib's default SSL context
# verifies it, instead of disabling verification outright.
COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

ENTRYPOINT ["python3", "/app/release.py"]
