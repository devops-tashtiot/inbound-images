FROM gcr.io/kaniko-project/executor:v1.23.2-debug

WORKDIR /app
ENV HOME=/root
ENV USER=root
ENV DOCKER_CONFIG=/kaniko/.docker/
ENV DOCKER_CREDENTIAL_GCR_CONFIG=/kaniko/.config/gcloud/docker_credential_gcr_config.json
COPY plugin.sh /app/plugin.sh

RUN chmod +x /app/plugin.sh

ENTRYPOINT [ "/app/plugin.sh" ]






