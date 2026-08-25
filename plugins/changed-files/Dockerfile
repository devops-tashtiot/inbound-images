FROM registry.access.redhat.com/ubi9/ubi-minimal:9.5-1745855087

WORKDIR /app

# ubi9-minimal ships update-ca-trust despite the name — trust the Cloudflare Origin CA
# so anything this plugin calls out to over *.devopstashtiot.page verifies TLS.
COPY cloudflare-origin-ca-rsa-root.pem /etc/pki/ca-trust/source/anchors/cloudflare-origin-ca-rsa-root.pem
RUN update-ca-trust

RUN microdnf -y install git && \
    microdnf clean all

COPY changed.sh changed.sh

RUN chmod +x changed.sh
RUN git config --global --add safe.directory '*'

ENTRYPOINT /app/changed.sh
