FROM quay.io/buildah/stable:latest

WORKDIR /app
ENV HOME=/root

COPY plugin.sh /app/plugin.sh
RUN chmod +x /app/plugin.sh

# This plugin pushes images to harbor.devopstashtiot.page, reached internally via
# ingress-nginx presenting a Cloudflare Origin CA-issued cert (not publicly trusted).
# Trust that root so `buildah push` verifies TLS instead of needing
# PLUGIN_SKIP_TLS_VERIFY. The base is Fedora-based (quay.io/buildah/stable), so use
# the ca-trust framework (update-ca-trust), not Debian/Alpine's update-ca-certificates.
COPY cloudflare-origin-ca-rsa-root.pem /etc/pki/ca-trust/source/anchors/cloudflare-origin-ca-rsa-root.pem
RUN update-ca-trust

ENTRYPOINT ["/app/plugin.sh"]
