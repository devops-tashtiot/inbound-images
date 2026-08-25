FROM docker.io/netanelzucaim123/python-git-cliff:latest

WORKDIR /app

# Build context must be the repo root:
#   docker build -f plugins/master-versions/Dockerfile -t <image> .
COPY release.py /app/release.py
COPY cliff.toml /app/cliff.toml

# release.py fetches the PR body over HTTPS from Bitbucket, which is reached
# internally via ingress-nginx presenting a Cloudflare Origin CA-issued cert
# (not publicly trusted). Trust that root so urllib's default SSL context
# verifies it, instead of disabling verification outright.
COPY cloudflare-origin-ca-rsa-root.pem /usr/local/share/ca-certificates/cloudflare-origin-ca-rsa-root.crt
RUN update-ca-certificates

ENTRYPOINT ["python3", "/app/release.py"]

