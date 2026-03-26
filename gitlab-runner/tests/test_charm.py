# Copyright 2021 Erik Lönroth
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
import pathlib
import os
import sys
import unittest
from unittest.mock import patch

import ops.testing
from ops.testing import Harness

# Set testing environmental variable
ops.testing.SIMULATE_CAN_CONNECT = True

# Get paths
current_path = pathlib.Path.cwd()
src_path = current_path.parent.joinpath('src')
templates_path = current_path.parent.joinpath('templates')

print(f"Current path: {current_path.as_posix()}\n"
      f"src path: {src_path.as_posix()}, Valid: {src_path.is_dir()}\n"
      f"Templates path: {templates_path.as_posix()}, Valid: {templates_path.is_dir()}")

sys.path.append(src_path.as_posix())
try:
    from charm import GitlabRunnerCharm
    from gitlab_runner import register_docker
except ImportError:
    print("ERROR: Import of charm.GitlabRunnerCharm failed!")
    raise


class MockCharm:

    def __init__(self):
        self.config = dict()
        self.config['gitlab-server'] = 'https://gitlab.com'
        self.config['gitlab-registration-token'] = 'abcdEFGH'
        self.config['tag-list'] = ""
        self.config['concurrent'] = 1
        self.config['run-untagged'] = True
        self.config['locked'] = True
        self.config['executor'] = "docker"

        self.config[''] = ""
        self.config['check-interval'] = 3
        self.config['sentry-dsn'] = True
        self.config['locked'] = True
        self.config['concurrent'] = 1
        self.config['log-level'] = "error"
        self.config['log-format'] = "docker:latest"
        self.config['docker-image'] = "docker:latest"
        self.config['docker-in-docker'] = False
        self.config['docker-tmpfs'] = "/scratch:rw,exec,size=1g"


class TestCharm(unittest.TestCase):

    def setUp(self):
        self._patch_write_runner_env_defaults = patch.object(
            GitlabRunnerCharm,
            '_write_runner_env_defaults',
            autospec=True,
        )
        self._patch_write_docker_proxy_dropin = patch.object(
            GitlabRunnerCharm,
            '_write_docker_proxy_dropin',
            autospec=True,
        )

        self.mock_write_runner_env_defaults = self._patch_write_runner_env_defaults.start()
        self.mock_write_docker_proxy_dropin = self._patch_write_docker_proxy_dropin.start()
        self.addCleanup(self._patch_write_runner_env_defaults.stop)
        self.addCleanup(self._patch_write_docker_proxy_dropin.stop)

        # Prevent unit tests from invoking real system binaries/services.
        self._patch_subprocess_run = patch('subprocess.run')
        self._patch_get_token = patch(
            'gitlab_runner.get_token',
            return_value='ABCDEFGH',
        )
        self._patch_registered = patch(
            'gitlab_runner.gitlab_runner_registered_already',
            return_value=False,
        )
        self._patch_check_mandatory = patch(
            'gitlab_runner.check_mandatory_config_values',
            return_value=True,
        )
        self._patch_check_tmpfs = patch(
            'gitlab_runner.check_docker_tmpfs_config',
            return_value=True,
        )
        self._patch_get_version = patch(
            'gitlab_runner.get_gitlab_runner_version',
            return_value='0.0.0',
        )

        self.mock_subprocess_run = self._patch_subprocess_run.start()
        self.mock_get_token = self._patch_get_token.start()
        self.mock_registered = self._patch_registered.start()
        self.mock_check_mandatory = self._patch_check_mandatory.start()
        self.mock_check_tmpfs = self._patch_check_tmpfs.start()
        self.mock_get_version = self._patch_get_version.start()

        self.addCleanup(self._patch_subprocess_run.stop)
        self.addCleanup(self._patch_get_token.stop)
        self.addCleanup(self._patch_registered.stop)
        self.addCleanup(self._patch_check_mandatory.stop)
        self.addCleanup(self._patch_check_tmpfs.stop)
        self.addCleanup(self._patch_get_version.stop)

        self.harness = Harness(GitlabRunnerCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch('subprocess.Popen')
    @patch('subprocess.run')
    @patch('gitlab_runner.get_token')
    def test_01_config_changed_docker(
        self, mock_subprocess_popen, mock_subprocess_run, mock_get_token
    ):
        # Mock return code from processes
        mock_subprocess_popen.return_value.returncode = 0
        mock_subprocess_run.return_value.returncode = 0
        mock_get_token.return_value = 'ABCDEFGH'

        harness = Harness(GitlabRunnerCharm)
        self.addCleanup(harness.cleanup)
        harness.begin()
        # self.assertEqual(list(harness.charm._stored.executor), [])
        harness.update_config({"gitlab-registration-token": "abc",
                               "gitlab-server": "https://gitlab.com",
                               "executor": "docker"})
        print(f" Unit status after config changed:\n\t{harness.charm.unit.status}")
        self.assertEqual(
            harness.charm.config["executor"],
            "docker",
            msg='Executor not as configured',
        )

    @patch('subprocess.Popen')
    @patch('subprocess.run')
    @patch('gitlab_runner.get_token')
    def test_02_config_changed_lxd(
        self, mock_subprocess_popen, mock_subprocess_run, mock_get_token
    ):
        # Mock return code from processes
        mock_subprocess_popen.return_value.returncode = 0
        mock_subprocess_run.return_value.returncode = 0
        mock_get_token.return_value = 'ABCDEFGH'

        harness = Harness(GitlabRunnerCharm)
        self.addCleanup(harness.cleanup)
        harness.begin()
        # self.assertEqual(list(harness.charm._stored.executor), [])
        harness.update_config({"gitlab-registration-token": "abc",
                               "gitlab-server": "https://gitlab.com",
                               "executor": "lxd"})
        print(f" Unit status after config changed:\n\t{harness.charm.unit.status}")
        self.assertEqual(
            harness.charm.config["executor"],
            "lxd",
            msg='Executor not as configured',
        )

    @patch('gitlab_runner._render_runner_templates')
    def test_20_templates_runner_templates(self, mock_render_runner_templates):
        mock_render_runner_templates.return_value = False
        test_charm = MockCharm()
        result = register_docker(test_charm)
        self.assertFalse(result, msg="Magically succeeded to render required templates")

    def test_30_get_proxy_env(self):
        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy.example.com:3128",
                "JUJU_CHARM_HTTPS_PROXY": "https://proxy.example.com:3129",
                "JUJU_CHARM_NO_PROXY": "localhost,127.0.0.1,.example.com",
            },
            clear=False,
        ):
            env = self.harness.charm._get_proxy_env()
            self.assertEqual(env["HTTP_PROXY"], "http://proxy.example.com:3128")
            self.assertEqual(env["http_proxy"], "http://proxy.example.com:3128")
            self.assertEqual(env["HTTPS_PROXY"], "https://proxy.example.com:3129")
            self.assertEqual(env["https_proxy"], "https://proxy.example.com:3129")
            self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1,.example.com")
            self.assertEqual(env["no_proxy"], "localhost,127.0.0.1,.example.com")

    def test_30b_get_proxy_env_juju_aliases(self):
        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://juju-proxy.example.com:8080",
                "JUJU_CHARM_HTTPS_PROXY": "https://juju-proxy.example.com:8443",
                "JUJU_CHARM_NO_PROXY": "localhost,.svc",
            },
            clear=False,
        ):
            env = self.harness.charm._get_proxy_env()
            self.assertEqual(env["HTTP_PROXY"], "http://juju-proxy.example.com:8080")
            self.assertEqual(env["HTTPS_PROXY"], "https://juju-proxy.example.com:8443")
            self.assertEqual(env["NO_PROXY"], "localhost,.svc")

    @patch('gitlab_runner.register_docker')
    def test_31_register_passes_proxy_to_docker(self, mock_register_docker):
        mock_register_docker.return_value = True
        self.harness.charm._stored.executor = 'docker'
        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy.example.com:3128",
                "JUJU_CHARM_HTTPS_PROXY": "https://proxy.example.com:3129",
                "JUJU_CHARM_NO_PROXY": "localhost,127.0.0.1",
            },
            clear=False,
        ):
            self.harness.charm.register()

        mock_register_docker.reset_mock()

        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy.example.com:3128",
                "JUJU_CHARM_HTTPS_PROXY": "https://proxy.example.com:3129",
                "JUJU_CHARM_NO_PROXY": "localhost,127.0.0.1",
            },
            clear=False,
        ):
            registered = self.harness.charm.register()

        self.assertTrue(registered)
        mock_register_docker.assert_called_once_with(
            self.harness.charm,
            http_proxy='http://proxy.example.com:3128',
            https_proxy='https://proxy.example.com:3129',
            no_proxy='localhost,127.0.0.1'
        )

    @patch('gitlab_runner.register_lxd')
    def test_32_register_passes_proxy_to_lxd(self, mock_register_lxd):
        mock_register_lxd.return_value = True
        self.harness.charm._stored.executor = 'lxd'
        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy.example.com:3128",
                "JUJU_CHARM_HTTPS_PROXY": "https://proxy.example.com:3129",
                "JUJU_CHARM_NO_PROXY": "localhost,127.0.0.1",
            },
            clear=False,
        ):
            self.harness.charm.register()

        mock_register_lxd.reset_mock()

        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy.example.com:3128",
                "JUJU_CHARM_HTTPS_PROXY": "https://proxy.example.com:3129",
                "JUJU_CHARM_NO_PROXY": "localhost,127.0.0.1",
            },
            clear=False,
        ):
            registered = self.harness.charm.register()

        self.assertTrue(registered)
        mock_register_lxd.assert_called_once_with(
            self.harness.charm,
            http_proxy='http://proxy.example.com:3128',
            https_proxy='https://proxy.example.com:3129',
            no_proxy='localhost,127.0.0.1'
        )
