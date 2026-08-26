#!/bin/sh
# inject-ca.sh — distro-detecting CA trust installer. Run inside the build, no network
# needed for the trust-store update itself (only microdnf/apk install steps need it).
# Generic across every base image family in this org's repos: RHEL/UBI (full and
# minimal), Debian/Ubuntu, Alpine, and images with no OS trust-store tooling at all
# (e.g. kaniko's debug image) that instead honor SSL_CERT_DIR directly. A Dockerfile
# using this never needs to know which of these applies — it just COPYs the cert and
# this script in, and RUNs it.
set -eu

CERT=/tmp/cloudflare-origin-ca-rsa-root.pem

if command -v update-ca-certificates >/dev/null 2>&1; then
    # Debian/Ubuntu family
    mkdir -p /usr/local/share/ca-certificates
    cp "$CERT" /usr/local/share/ca-certificates/cloudflare-origin-ca-rsa-root.crt
    update-ca-certificates
elif command -v update-ca-trust >/dev/null 2>&1; then
    # RHEL/UBI family
    mkdir -p /etc/pki/ca-trust/source/anchors
    cp "$CERT" /etc/pki/ca-trust/source/anchors/cloudflare-origin-ca-rsa-root.pem
    update-ca-trust
elif [ -f /etc/os-release ] && grep -qi 'rhel\|centos\|fedora' /etc/os-release 2>/dev/null; then
    # Stripped RHEL-minimal images (e.g. ubi-minimal) ship neither update-ca-trust nor a
    # package manager with cached metadata — install ca-certificates first, then trust it.
    (microdnf -y install ca-certificates 2>/dev/null || dnf -y install ca-certificates 2>/dev/null || yum -y install ca-certificates)
    mkdir -p /etc/pki/ca-trust/source/anchors
    cp "$CERT" /etc/pki/ca-trust/source/anchors/cloudflare-origin-ca-rsa-root.pem
    update-ca-trust
elif [ -n "${SSL_CERT_DIR:-}" ]; then
    # No OS trust-store tooling at all — no update-ca-certificates, no update-ca-trust,
    # often not even /etc/os-release (e.g. kaniko's distroless+busybox debug image) —
    # but the runtime itself (Go's crypto/x509) reads SSL_CERT_DIR directly as a
    # directory of PEM certs, so trusting an extra one is just dropping the file in;
    # no "update" command exists or is needed.
    mkdir -p "$SSL_CERT_DIR"
    cp "$CERT" "$SSL_CERT_DIR/cloudflare-origin-ca-rsa-root.pem"
elif [ -f /etc/ssl/certs/ca-certificates.crt ]; then
    # Alpine-derived images that ship the bundle but not the ca-certificates package
    # (no update-ca-certificates binary, no network for apk) — append directly.
    cat "$CERT" >> /etc/ssl/certs/ca-certificates.crt
else
    echo "inject-ca.sh: no known trust-store mechanism found on this image" >&2
    exit 1
fi
