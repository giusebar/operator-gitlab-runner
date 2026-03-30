#!/usr/bin/env bash

# /opt/lxd-executor/prepare.sh

currentDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
source ${currentDir}/base.sh # Get variables from base.

set -eo pipefail

# trap any error, and mark it as a system failure.
trap "exit $SYSTEM_FAILURE_EXIT_CODE" ERR

# default to Ubuntu 22.04 if none has been set with the 'image' keyword in the .gitlab-ci.yml
CUSTOM_ENV_CI_JOB_IMAGE="${CUSTOM_ENV_CI_JOB_IMAGE:-ubuntu:22.04}"

prepare_network () {

    # prevent name collisions when using nested LXD on .lxd
    lxc network set lxdbr0 dns.domain juju-gitlab-runner 

}

start_container () {
    if lxc info "$CONTAINER_ID" >/dev/null 2>/dev/null ; then
        echo 'Found old container, deleting'
        lxc delete -f "$CONTAINER_ID"
    fi

    # make sure profile is configured correctly
    if lxc profile show gitlab > /dev/null 2> /dev/null ; then
        echo 'Found existing profile, skipping creation'
    else
	lxc profile create gitlab
    fi
    lxc profile set gitlab security.nesting true
    lxc profile set gitlab security.privileged true
    printf "lxc.apparmor.profile=unconfined\nlxc.mount.auto=sys:rw\n" | lxc profile set gitlab raw.lxc -

    lxc launch "$CUSTOM_ENV_CI_JOB_IMAGE" "$CONTAINER_ID" -p gitlab -p default

    # Wait for container to start, we are using systemd to check this,
    # for the sake of brevity.
    for i in $(seq 1 10); do
        if lxc exec "$CONTAINER_ID" -- sh -c "systemctl isolate multi-user.target" >/dev/null 2>/dev/null; then
            break
        fi

        if [ "$i" == "10" ]; then
            echo 'Waited for 10 seconds to start container, exiting..'
            # Inform GitLab Runner that this is a system failure, so it
            # should be retried.
            exit "$SYSTEM_FAILURE_EXIT_CODE"
        fi

        sleep 1s
    done
}

set_proxy_env () {
    local updated=0

    if [ -n "${HTTP_PROXY:-}" ]; then
        lxc config set "$CONTAINER_ID" environment.HTTP_PROXY "$HTTP_PROXY"
        lxc config set "$CONTAINER_ID" environment.http_proxy "$HTTP_PROXY"
        updated=1
    fi

    if [ -n "${HTTPS_PROXY:-}" ]; then
        lxc config set "$CONTAINER_ID" environment.HTTPS_PROXY "$HTTPS_PROXY"
        lxc config set "$CONTAINER_ID" environment.https_proxy "$HTTPS_PROXY"
        updated=1
    fi

    if [ -n "${NO_PROXY:-}" ]; then
        lxc config set "$CONTAINER_ID" environment.NO_PROXY "$NO_PROXY"
        lxc config set "$CONTAINER_ID" environment.no_proxy "$NO_PROXY"
        updated=1
    fi

    if [ "$updated" -eq 1 ]; then
        lxc restart "$CONTAINER_ID"
    fi
}

install_dependencies () {
    # Refresh apt metadata in the new container before installing packages.
    lxc exec "$CONTAINER_ID" -- sh -ec "export DEBIAN_FRONTEND=noninteractive; apt-get update -y"

    # Install Git LFS; retry with upstream repository bootstrap when needed.
    lxc exec "$CONTAINER_ID" -- sh -ec "export DEBIAN_FRONTEND=noninteractive; apt-get install -y git-lfs || (curl -fsSL https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | bash && apt-get update -y && apt-get install -y git-lfs)"

    # Install gitlab-runner for cache/artifacts support without relying on S3 downloads.
    lxc exec "$CONTAINER_ID" -- sh -ec "export DEBIAN_FRONTEND=noninteractive; apt-get install -y gitlab-runner || (curl -fsSL https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh | bash && apt-get update -y && apt-get install -y gitlab-runner)"
}

echo "Running in $CONTAINER_ID"

prepare_network

start_container

set_proxy_env

install_dependencies
