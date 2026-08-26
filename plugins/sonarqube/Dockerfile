FROM harbor.devopstashtiot.page/base/ubi9:v1.0.0
# NOTE: this FROM path is stale — it's the old a-woodpecker-plugins publishing scheme.
# ubi9 now lives in outbound-images-with-ca under a different Harbor project/tag
# convention entirely. Left as-is (not this fix's scope) rather than guessing which
# pinned version to point at instead — the explicit CA injection below no longer
# depends on this base image trusting the CA transitively either way.

# SonarQube scanner plugin. The SonarScanner CLI is downloaded at build time from the
# public SonarSource CDN (needs outbound internet during the buildah/kaniko build). The
# CDN artifact is named sonar-scanner-cli-<ver> but unzips to the sonar-scanner-<ver>
# directory.
ENV CERT_PATH=/etc/pki/ca-trust/source/anchors/ \
    SONAR_CLI=https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-7.1.0.4889-linux-x64.zip

COPY cloudflare-origin-ca-rsa-root.pem /tmp/cloudflare-origin-ca-rsa-root.pem
COPY inject-ca.sh /tmp/inject-ca.sh
RUN sh /tmp/inject-ca.sh && rm -f /tmp/inject-ca.sh /tmp/cloudflare-origin-ca-rsa-root.pem

COPY sonar-scan.sh /sonar-scan.sh

RUN dnf install -y nodejs wget unzip \
 && wget -O /tmp/sonar-scanner.zip "$SONAR_CLI" \
 && unzip /tmp/sonar-scanner.zip -d /opt/ \
 && rm -f /tmp/sonar-scanner.zip \
 && chmod -R +x /opt/sonar-scanner-7.1.0.4889-linux-x64 \
 && chmod +x /sonar-scan.sh \
 && dnf clean all

ENV PATH="/opt/sonar-scanner-7.1.0.4889-linux-x64/bin:${PATH}"
ENV JAVA_HOME="/opt/sonar-scanner-7.1.0.4889-linux-x64/jre/bin"

USER 0

CMD /sonar-scan.sh
