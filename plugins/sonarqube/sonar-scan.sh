#!/bin/bash
set -e

function validate_flags() {
    if [[ ! "$1" =~ (^| )-D[a-zA-Z0-9_.]+=.* ]]; then
        echo "Invalid format in extra_properties. Must be in the form -Dkey=value"
        exit 1
    fi
}

if [[ -z "$PLUGIN_SONAR_HOST" ]]; then
    echo "Settings - sonar_host variable is required"
    exit 1
fi

if [[ -z "$PLUGIN_SONAR_TOKEN" ]]; then
    echo "Settings - sonar_token variable is required"
    exit 1
fi

SONAR_CMD="sonar-scanner -Dsonar.scanner.skipJreProvisioning=true -Dsonar.host.url=$PLUGIN_SONAR_HOST -Dsonar.token=$PLUGIN_SONAR_TOKEN"

if [[ -z "$CI_COMMIT_PULL_REQUEST" ]]; then
    SONAR_CMD+=" -Dsonar.branch.name=$CI_COMMIT_BRANCH"
fi

if [[ -n "$CI_COMMIT_PULL_REQUEST" ]]; then
    SONAR_CMD+=" -Dsonar.pullrequest.key=$CI_COMMIT_PULL_REQUEST -Dsonar.pullrequest.branch=$CI_COMMIT_BRANCH"
fi

if [[ -n "$PLUGIN_EXTRA_PROPERTIES" ]]; then
    validate_flags "$PLUGIN_EXTRA_PROPERTIES"
    SONAR_CMD+=" $PLUGIN_EXTRA_PROPERTIES"
fi

eval "$SONAR_CMD"
