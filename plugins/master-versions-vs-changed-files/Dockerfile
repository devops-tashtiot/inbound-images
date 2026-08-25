FROM docker.io/library/alpine:3.19

RUN apk add --no-cache bash

COPY check.sh /app/check.sh
RUN chmod +x /app/check.sh

ENTRYPOINT ["/app/check.sh"]
