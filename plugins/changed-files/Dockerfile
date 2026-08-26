FROM registry.access.redhat.com/ubi9/ubi-minimal:9.5-1745855087

WORKDIR /app

COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

RUN microdnf -y install git && \
    microdnf clean all

COPY changed.sh changed.sh

RUN chmod +x changed.sh
RUN git config --global --add safe.directory '*'

ENTRYPOINT /app/changed.sh
