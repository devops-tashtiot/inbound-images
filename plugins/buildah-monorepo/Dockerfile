FROM quay.io/buildah/stable:latest

WORKDIR /app
ENV HOME=/root

COPY plugin.sh /app/plugin.sh
RUN chmod +x /app/plugin.sh

ENTRYPOINT ["/app/plugin.sh"]
