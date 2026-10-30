from pathlib import Path

import pytest

from signal_harness.errors import ConfigurationError
from signal_harness.forge import remote_location
from signal_harness.git import normalize_remote, remote_clone_url


def test_local_bare_remote_has_transport_independent_identity(tmp_path):
    remote = tmp_path / "remote.git"
    assert normalize_remote(str(remote)) == normalize_remote(remote.as_uri())
    assert normalize_remote(str(remote)) != normalize_remote(str(tmp_path / "remote"))


def test_explicit_relative_git_paths_are_local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert normalize_remote("./remote.git") == normalize_remote(str(tmp_path / "remote.git"))
    assert normalize_remote("../remote.git") == normalize_remote(
        str(tmp_path.parent / "remote.git")
    )
    assert normalize_remote(Path("~/remote.git").expanduser().as_uri()) == normalize_remote(
        "~/remote.git"
    )


@pytest.mark.parametrize("remote", ["file://other-host/tmp/repo", "file:///tmp/repo?token=x"])
def test_local_uri_cannot_smuggle_network_identity_or_credentials(remote):
    with pytest.raises(ConfigurationError):
        normalize_remote(remote)


def test_local_git_identity_does_not_become_a_hosted_project(tmp_path):
    with pytest.raises(ConfigurationError):
        remote_location(str(tmp_path / "remote.git"))


def test_explicit_relative_clone_paths_do_not_become_github_projects(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert remote_clone_url("./remote.git") == str(tmp_path / "remote.git")
    assert remote_clone_url("../remote.git") == str(tmp_path.parent / "remote.git")
    assert remote_clone_url("~/remote.git") == str(Path("~/remote.git").expanduser())
