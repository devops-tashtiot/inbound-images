FROM docker.io/library/alpine:3.19

COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

RUN apk add --no-cache bash

COPY check.sh /app/check.sh
RUN chmod +x /app/check.sh

ENTRYPOINT ["/app/check.sh"]
