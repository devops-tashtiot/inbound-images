FROM gcr.io/kaniko-project/executor:v1.23.2-debug

WORKDIR /app
ENV HOME=/root
ENV USER=root
ENV DOCKER_CONFIG=/kaniko/.docker/
ENV DOCKER_CREDENTIAL_GCR_CONFIG=/kaniko/.config/gcloud/docker_credential_gcr_config.json

COPY plugin.sh /app/plugin.sh
RUN chmod +x /app/plugin.sh

# This image has no OS CA-trust tooling at all (no update-ca-certificates, no
# update-ca-trust, no /etc/os-release) — inject-ca.sh detects that and falls back to
# SSL_CERT_DIR (which this image sets), which Go's crypto/x509 reads directly.
COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

ENTRYPOINT [ "/app/plugin.sh" ]
