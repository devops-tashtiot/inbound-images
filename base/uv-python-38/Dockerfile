FROM docker.io/library/python:3.8-slim

# Python 3.8 base with the uv package manager (pinned 0.11.29) installed via pip.
COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

RUN pip install --no-cache-dir "uv==0.11.29"
