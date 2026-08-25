FROM gcr.io/kaniko-project/executor:v1.23.2-debug

WORKDIR /app
ENV HOME=/root
ENV USER=root
ENV DOCKER_CONFIG=/kaniko/.docker/
ENV DOCKER_CREDENTIAL_GCR_CONFIG=/kaniko/.config/gcloud/docker_credential_gcr_config.json
COPY plugin.sh /app/plugin.sh

RUN chmod +x /app/plugin.sh

# The kaniko debug image has no OS CA-trust tooling at all (no update-ca-certificates,
# no update-ca-trust, no /etc/os-release — it's built on distroless+busybox). It sets
# SSL_CERT_DIR=/kaniko/ssl/certs, which Go's crypto/x509 reads directly as a directory
# of PEM certs — so trusting an extra CA here is just dropping the file in, no "update"
# command exists or is needed.
COPY cloudflare-origin-ca-rsa-root.pem /kaniko/ssl/certs/cloudflare-origin-ca-rsa-root.pem

ENTRYPOINT [ "/app/plugin.sh" ]
