FROM docker.io/library/python:3.9-slim

# Python 3.9 base with the uv package manager (pinned 0.11.29) installed via pip.
# Cloudflare Origin CA baked into the Debian trust store (update-ca-certificates,
# local/no-network) so tooling that reaches a *.devopstashtiot.page service verifies
# TLS. pip install needs PyPI network, which the buildah build has.
COPY cloudflare-origin-ca-rsa-root.pem /usr/local/share/ca-certificates/cloudflare-origin-ca-rsa-root.crt
RUN update-ca-certificates \
 && pip install --no-cache-dir "uv==0.11.29"
