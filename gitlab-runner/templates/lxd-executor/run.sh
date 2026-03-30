#!/usr/bin/env bash

# /opt/lxd-executor/run.sh

currentDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
source ${currentDir}/base.sh # Get variables from base.

append_no_proxy_host() {
    local value="$1"
    local host="$2"

    if [ -z "$host" ]; then
        echo "$value"
        return
    fi

    case ",$value," in
        *",$host,"*)
            echo "$value"
            ;;
        *)
            if [ -z "$value" ]; then
                echo "$host"
            else
                echo "$value,$host"
            fi
            ;;
    esac
}

# Forward all CUSTOM_ENV_* vars as regular env vars expected by GitLab job scripts.
env_args=()
while IFS='=' read -r key value; do
    if [[ "$key" == CUSTOM_ENV_* ]]; then
        env_args+=(--env "${key#CUSTOM_ENV_}=${value}")
    fi
done < <(env)

# Make sure the GitLab host bypasses HTTP(S) proxy to avoid 403 from proxy ACLs
# when cloning from internal/private GitLab endpoints.
gitlab_host="${CUSTOM_ENV_CI_SERVER_HOST:-}"
if [ -z "$gitlab_host" ] && [ -n "${CUSTOM_ENV_CI_REPOSITORY_URL:-}" ]; then
    gitlab_host="$(echo "${CUSTOM_ENV_CI_REPOSITORY_URL}" | sed -E 's#^[a-zA-Z]+://([^/:]+).*#\1#')"
fi

effective_no_proxy="$(append_no_proxy_host "${CUSTOM_ENV_NO_PROXY:-}" "$gitlab_host")"
if [ -n "$effective_no_proxy" ]; then
    env_args+=(--env "NO_PROXY=${effective_no_proxy}")
    env_args+=(--env "no_proxy=${effective_no_proxy}")
fi

lxc exec "$CONTAINER_ID" "${env_args[@]}" -- /bin/bash < "${1}"
if [ $? -ne 0 ]; then
    # Exit using the variable, to make the build as failure in GitLab
    # CI.
    exit $BUILD_FAILURE_EXIT_CODE
fi
