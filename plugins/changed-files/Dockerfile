FROM registry.access.redhat.com/ubi9/ubi-minimal:9.5-1745855087

WORKDIR /app

RUN microdnf -y install git && \
    microdnf clean all

COPY changed.sh changed.sh

RUN chmod +x changed.sh
RUN git config --global --add safe.directory '*'

ENTRYPOINT /app/changed.sh


























