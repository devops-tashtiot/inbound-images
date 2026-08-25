# sonarqube plugin

Woodpecker plugin that runs a [SonarScanner CLI](https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/scanners/sonarscanner/)
analysis against a SonarQube server. The scanner (`sonar-scanner-7.1.0.4889`) is baked into the
image at build time (downloaded from the public SonarSource CDN); the entrypoint `sonar-scan.sh`
builds the `sonar-scanner` command from the settings below and runs it.

The image is built `FROM harbor.devopstashtiot.page/base/ubi9:v1.0.0`, which already trusts the
Cloudflare Origin CA, so at run time it can reach a `*.devopstashtiot.page` SonarQube host over
its Origin-CA-issued cert. The build itself needs outbound internet to fetch the scanner zip
from `binaries.sonarsource.com`.

## Settings

| Setting | Env var | Required | Description |
|---------|---------|----------|-------------|
| `sonar_host` | `PLUGIN_SONAR_HOST` | Yes | SonarQube server URL (e.g. `https://sonarqube.devopstashtiot.page`) |
| `sonar_token` | `PLUGIN_SONAR_TOKEN` | Yes | Analysis token. Pass via `from_secret`. |
| `extra_properties` | `PLUGIN_EXTRA_PROPERTIES` | No | Extra `-Dkey=value` scanner flags. Validated to the `-Dkey=value` form. |

Branch / PR context is picked up automatically from Woodpecker's `CI_COMMIT_BRANCH` and
`CI_COMMIT_PULL_REQUEST` (sets `sonar.branch.name` on a branch build, or
`sonar.pullrequest.key` / `sonar.pullrequest.branch` on a PR build).

Project metadata (`sonar.projectKey`, `sonar.sources`, coverage/xunit report paths, …) is read
from a `sonar-project.properties` file in the repo being scanned.

## Example

```yaml
- name: SonarQube
  image: harbor.devopstashtiot.page/plugins/sonarqube:v1.0.0
  settings:
    sonar_host: https://sonarqube.devopstashtiot.page
    sonar_token:
      from_secret: sonar_token
    extra_properties: -Dsonar.projectVersion=1.2.3
```
