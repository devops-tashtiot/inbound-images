FROM quay.io/buildah/stable:latest

WORKDIR /app
ENV HOME=/root

COPY plugin.sh /app/plugin.sh
RUN chmod +x /app/plugin.sh

COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

ENTRYPOINT ["/app/plugin.sh"]
