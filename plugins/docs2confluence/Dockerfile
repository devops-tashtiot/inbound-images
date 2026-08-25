FROM registry.access.redhat.com/ubi9/ubi:9.2-696

ENV PANDOC_URL=https://github.com/jgm/pandoc/releases/download/3.8/pandoc-3.8-linux-amd64.tar.gz

WORKDIR /app

# Trust the Cloudflare Origin CA so this plugin's calls to *.devopstashtiot.page
# (the Confluence API it pushes docs to) verify TLS.
COPY cloudflare-origin-ca-rsa-root.pem /etc/pki/ca-trust/source/anchors/cloudflare-origin-ca-rsa-root.pem
RUN update-ca-trust

RUN dnf -y install wget jq bash && \
    dnf clean all && \
    wget -O /tmp/pandoc-3.8-linux-amd64.tar.gz $PANDOC_URL && \
    tar -xzvf /tmp/pandoc-3.8-linux-amd64.tar.gz -C /tmp && \
    mv /tmp/pandoc-3.8/bin/pandoc /usr/local/bin/pandoc && \
    rm -rf /tmp/pandoc-3.8-linux-amd64.tar.gz /tmp/pandoc-3.8 && \
    chmod -R 755 /usr/local/bin/pandoc
COPY docs2confluence.sh docs2confluence.sh
RUN chmod +x docs2confluence.sh

CMD /app/docs2confluence.sh
