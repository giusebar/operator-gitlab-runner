#!/usr/bin/env python3
# Copyright 2021 Erik Lönroth
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk
import logging
import os
import pathlib
import stat
import re
import glob
import shutil
import socket
import subprocess
import toml
from pathlib import Path
import jinja2

PROXY_ENV_KEYS = {
    'HTTP_PROXY',
    'http_proxy',
    'HTTPS_PROXY',
    'https_proxy',
    'NO_PROXY',
    'no_proxy',
}


def install_lxd_executor(env=None):
    subprocess.run(['useradd', '-g', 'lxd', 'gitlab-runner'])
    subprocess.run(['mkdir', '-p', '/opt/lxd-executor'])
    for file in glob.glob('templates/lxd-executor/*.sh'):
        f = Path(file)
        installed_file = Path(shutil.copy2(f, '/opt/lxd-executor/'))
        installed_file.chmod(stat.S_IEXEC)
    subprocess.run(['lxd', 'init', '--auto'], env=env)


def proxy_environment_variables(proxy_env=None):
    proxy_env = proxy_env or {}
    http_proxy = proxy_env.get('http_proxy') or proxy_env.get('HTTP_PROXY')
    https_proxy = proxy_env.get('https_proxy') or proxy_env.get('HTTPS_PROXY')
    no_proxy = proxy_env.get('no_proxy') or proxy_env.get('NO_PROXY')

    variables = {}
    if http_proxy:
        variables['http_proxy'] = http_proxy
        variables['HTTP_PROXY'] = http_proxy
    if https_proxy:
        variables['HTTPS_PROXY'] = https_proxy
        variables['https_proxy'] = https_proxy
    if no_proxy:
        variables['NO_PROXY'] = no_proxy
        variables['no_proxy'] = no_proxy

    return variables


def configure_lxd_proxy(proxy_env=None):
    proxy_env = proxy_environment_variables(proxy_env)
    lxd_proxy_map = {
        'core.proxy_http': proxy_env.get('HTTP_PROXY'),
        'core.proxy_https': proxy_env.get('HTTPS_PROXY'),
        'core.proxy_ignore_hosts': proxy_env.get('NO_PROXY'),
    }

    for lxd_key, value in lxd_proxy_map.items():
        if value:
            subprocess.run(['lxc', 'config', 'set', lxd_key, value])
        else:
            subprocess.run(['lxc', 'config', 'unset', lxd_key])


def _docker_runner_proxy_environment_entries(proxy_env=None):
    return [f'{key}={value}' for key, value in proxy_environment_variables(proxy_env).items()]


def _set_docker_runner_proxy_environment(runner, proxy_entries):
    current_env = runner.get('environment')
    if not isinstance(current_env, list):
        current_env = []

    preserved = []
    for entry in current_env:
        if not isinstance(entry, str):
            preserved.append(entry)
            continue
        key, sep, _ = entry.partition('=')
        if sep and key in PROXY_ENV_KEYS:
            continue
        preserved.append(entry)

    new_env = preserved + proxy_entries
    if new_env == runner.get('environment'):
        return False

    if new_env:
        runner['environment'] = new_env
    elif 'environment' in runner:
        del runner['environment']
    return True


def configure_docker_runner_proxy_env(proxy_env=None,
                                      config_path='/etc/gitlab-runner/config.toml') -> bool:
    """Append proxy environment variables to docker runners in config.toml."""
    desired_entries = _docker_runner_proxy_environment_entries(proxy_env)

    try:
        with open(config_path, encoding='utf-8') as f:
            data = toml.load(f)
    except (OSError, toml.TomlDecodeError, IndexError) as e:
        logging.warning(
            'Unable to load %s while applying docker proxy environment: %s',
            config_path,
            e,
        )
        return False

    runners = data.get('runners')
    if not isinstance(runners, list):
        return False

    changed = False
    for runner in runners:
        if not isinstance(runner, dict):
            continue

        if runner.get('executor') != 'docker':
            continue

        changed = _set_docker_runner_proxy_environment(runner, desired_entries) or changed

    if not changed:
        return True

    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            toml.dump(data, f)
    except OSError as e:
        logging.warning(
            'Unable to persist docker proxy environment to %s: %s',
            config_path,
            e,
        )
        return False

    return True


def install_docker_executor(env=None):
    subprocess.run(['apt', 'install', '-y', 'docker.io'], env=env)
    subprocess.run(['systemctl', 'start', 'docker.service'])


def get_gitlab_runner_version():
    cmd = "gitlab-runner --version"
    r = subprocess.run(cmd.split(),
                       stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT,
                       universal_newlines=True)
    return re.search('Version:(.*)', r.stdout).group(1).lstrip()


def check_mandatory_config_values(charm) -> bool:
    nonempty = list()
    nonempty.append(charm.config['gitlab-registration-token'])
    nonempty.append(charm.config['gitlab-server'])
    nonempty.append(charm.config['executor'])
    return all(nonempty)


def check_docker_tmpfs_config(charm) -> bool:
    if charm.config['docker-tmpfs'] != '':
        try:
            a, b = charm.config['docker-tmpfs'].split(':')
        except ValueError:
            return False
    return True


def gitlab_runner_registered_already() -> bool:
    hostname_fqdn = socket.getfqdn()
    cp = subprocess.run(("gitlab-runner verify -n " + hostname_fqdn).split())
    return cp.returncode == 0


def _render_runner_templates(charm) -> bool:
    def _render_templates(_template_path: pathlib.Path,
                          _template_filename: str,
                          _rendered_target_path: pathlib.Path,
                          _keywords) -> bool:
        try:
            # Load template
            template = jinja2.Environment(
                loader=jinja2.FileSystemLoader(_template_path,),
                undefined=jinja2.StrictUndefined
            ).get_template(_template_filename)
            # Redner template
            rendered_template = template.render(_keywords)
            rendered_target_path.write_text(rendered_template)

            return True
        except jinja2.exceptions.TemplateNotFound:
            logging.error(f"Template {_template_filename} could not be found.")
            return False
        except jinja2.exceptions.TemplateSyntaxError as e:
            logging.error(
                f'Template {_template_filename} could not be rendered due to '
                f'syntax error\n\tProblem: {e}'
            )
            return False
        except jinja2.exceptions.UndefinedError as e:
            logging.error(
                f'Template {_template_filename} could not be rendered due to '
                f'syntax error\n\tProblem: {e}'
            )
            return False
        except jinja2.TemplateError as e:
            logging.error(f'Template {template_filename} could not be rendered\n'
                          f'\tProblem: {e}')
            return False

    # Render #1 - global runner config
    sentry_dsn = charm.config['sentry-dsn']
    if not isinstance(sentry_dsn, str):
        sentry_dsn = ''

    template_path = Path('templates/etc/gitlab-runner/')
    template_filename = 'config.toml'
    rendered_target_path = Path('/etc/gitlab-runner/config.toml')
    keywords_to_render = {'concurrent': charm.config['concurrent'],
                          'checkinterval': charm.config['check-interval'],
                          'sentrydsn': sentry_dsn,
                          'loglevel': charm.config['log-level'],
                          'logformat': charm.config['log-format']}
    if not _render_templates(template_path,
                             template_filename,
                             rendered_target_path,
                             keywords_to_render):
        return False

    if charm.config['executor'] == 'docker':
        # Render #2 - runner template.
        template_path = Path('templates/runner-templates/')
        template_filename = 'docker-1.template'
        rendered_target_path = Path('/tmp/runner-template-config.toml')

        keywords_to_render = {'docker_image': charm.config['docker-image']}
        # If tmpfs was defined for Docker executor, render required config.
        if charm.config['docker-tmpfs'] != '':
            docker_tmpfs_path, docker_tmpfs_config = charm.config[
                'docker-tmpfs'
            ].split(':')
            keywords_to_render['docker_tmpfs_path'] = docker_tmpfs_path
            keywords_to_render['docker_tmpfs_config'] = docker_tmpfs_config
        # If docker-in-docker is allowed
        if isinstance(charm.config['docker-in-docker'], bool) and charm.config['docker-in-docker']:
            keywords_to_render['docker_in_docker'] = True

        if not _render_templates(template_path,
                                 template_filename,
                                 rendered_target_path,
                                 keywords_to_render):
            return False

    return True


def register_docker(charm, https_proxy=None, http_proxy=None, no_proxy=None) -> bool:

    # Render Gitlab runner templates
    if not _render_runner_templates(charm):
        return False

    hostname_fqdn = socket.getfqdn()
    gitlab_server = charm.config['gitlab-server']
    gitlab_registration_token = charm.config['gitlab-registration-token']
    tag_list = charm.config['tag-list']
    concurrent = charm.config['concurrent']
    run_untagged = charm.config['run-untagged']
    locked = charm.config['locked']
    runner_env = os.environ.copy()
    if http_proxy:
        runner_env['HTTP_PROXY'] = http_proxy
        runner_env['http_proxy'] = http_proxy
    if https_proxy:
        runner_env['HTTPS_PROXY'] = https_proxy
        runner_env['https_proxy'] = https_proxy
    if no_proxy:
        runner_env['NO_PROXY'] = no_proxy
        runner_env['no_proxy'] = no_proxy

    cmd = ["gitlab-runner", "register",
           "--non-interactive",
           "--config", "/etc/gitlab-runner/config.toml",
           "--template-config", "/tmp/runner-template-config.toml",
           "--name", f"{hostname_fqdn}",
           "--url", f"{gitlab_server}",
           "--registration-token", f"{gitlab_registration_token}",
           "--request-concurrency", f"{concurrent}",
           f"--run-untagged={run_untagged}",
           f"--locked={locked}",
           "--executor", "docker"]

    if not run_untagged and tag_list != "":
        cmd.extend(["--tag-list", "{tag-list}"])
    if run_untagged and tag_list != "":
        logging.warning(
            'Conflicting configuration, run-untagged=True and tag_list are '
            'mutually exclusive. Skipping tag-list.'
        )

    logging.info(
        "Executing registration call for gitlab-runner with Docker executor"
    )
    process = subprocess.Popen(cmd, env=runner_env)
    try:
        std_out, std_err = process.communicate(timeout=30)
        if std_out:
            logging.info(std_out)
        if std_err:
            logging.error(std_err)
    except subprocess.TimeoutExpired:
        process.kill()
        logging.error('Registration of gitlab-runner timed out and failed')
        return False

    logging.info(
        f'Registration of Docker executor finished with exit code: '
        f'{process.returncode}'
    )
    return process.returncode == 0


def register_lxd(charm, https_proxy=None, http_proxy=None, no_proxy=None) -> bool:

    # Render Gitlab runner templates
    if not _render_runner_templates(charm):
        return False

    hostname_fqdn = socket.getfqdn()
    gitlab_server = charm.config['gitlab-server']
    gitlab_registration_token = charm.config['gitlab-registration-token']
    tag_list = charm.config['tag-list']
    concurrent = charm.config['concurrent']
    run_untagged = charm.config['run-untagged']
    locked = charm.config['locked']
    runner_env = os.environ.copy()
    if http_proxy:
        runner_env['HTTP_PROXY'] = http_proxy
        runner_env['http_proxy'] = http_proxy
    if https_proxy:
        runner_env['HTTPS_PROXY'] = https_proxy
        runner_env['https_proxy'] = https_proxy
    if no_proxy:
        runner_env['NO_PROXY'] = no_proxy
        runner_env['no_proxy'] = no_proxy

    cmd = ["gitlab-runner", "register",
           "--non-interactive",
           "--config", "/etc/gitlab-runner/config.toml",
           "--name", f"{hostname_fqdn}",
           "--url", f"{gitlab_server}",
           "--registration-token", f"{gitlab_registration_token}",
           "--request-concurrency", f"{concurrent}",
           f"--run-untagged={run_untagged}",
           f"--locked={locked}",
           "--executor", "custom",
           "--builds-dir", "/builds",
           "--cache-dir", "/cache",
           "--custom-run-exec", "/opt/lxd-executor/run.sh",
           "--custom-prepare-exec", "/opt/lxd-executor/prepare.sh",
           "--custom-cleanup-exec", "/opt/lxd-executor/cleanup.sh",
           ]

    if not run_untagged and tag_list != "":
        cmd.extend(["--tag-list", "{tag-list}"])
    if run_untagged and tag_list != "":
        logging.warning(
            'Conflicting configuration, run-untagged=True and tag_list are '
            'mutually exclusive. Skipping tag-list.'
        )

    logging.info("Executing registration call for gitlab-runner with lxd executor")
    process = subprocess.Popen(cmd, env=runner_env)
    try:
        std_out, std_err = process.communicate(timeout=30)
        if std_out:
            logging.info(std_out)
        if std_err:
            logging.error(std_err)
    except subprocess.TimeoutExpired:
        process.kill()
        logging.error('Registration of gitlab-runner timed out and failed')
        return False

    logging.info(
        f'Registration of lxd executor finished with exit code: '
        f'{process.returncode}'
    )
    return process.returncode == 0


def get_token() -> str:
    """
    Returns: The 8 first chars of the token
    """
    with open('/etc/gitlab-runner/config.toml') as f:
        try:
            data = toml.load(f)
            return data['runners'][0]['token'][0:8]
        except KeyError as e:
            return str(e)


def unregister() -> bool:
    hostname_fqdn = socket.getfqdn()
    cmd = f"gitlab-runner unregister -n {hostname_fqdn} --all-runners"
    cp = subprocess.run(cmd.split())
    logging.debug(cp.stdout)
    return cp.returncode == 0
