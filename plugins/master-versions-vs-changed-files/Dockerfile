FROM docker.io/library/alpine:3.19

# alpine ships ca-certificates-bundle (so /etc/ssl/certs/ca-certificates.crt exists)
# but not the ca-certificates package, so update-ca-certificates is absent. Append
# directly to the existing bundle instead — needs no package, no network.
COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
RUN cat /tmp/cloudflare-origin-ca-rsa-root.pem >> /etc/ssl/certs/ca-certificates.crt \
 && rm /tmp/cloudflare-origin-ca-rsa-root.pem

RUN apk add --no-cache bash

COPY check.sh /app/check.sh
RUN chmod +x /app/check.sh

ENTRYPOINT ["/app/check.sh"]
