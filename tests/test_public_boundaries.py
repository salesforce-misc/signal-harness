from unittest.mock import patch

import pytest

from signal_harness.errors import ConfigurationError
from signal_harness.planner import _local_ci_context
from signal_harness.pull_requests import pull_request_plan
from signal_harness.security import redact_text, redact_value


@pytest.mark.parametrize(
    ("remote", "forge", "executable", "target"),
    [
        ("org/repo", "github", "gh", "org/repo"),
        ("git@github.example:org/repo.git", "github", "gh", "github.example/org/repo"),
        (
            "git@gitlab.example:group/sub/repo.git",
            "gitlab",
            "glab",
            "gitlab.example/group/sub/repo",
        ),
    ],
)
def test_change_request_plan_selects_provider(tmp_path, remote, forge, executable, target):
    body = tmp_path / "body.md"
    body.write_text("Description\nwith lines", encoding="utf-8")
    repo = {"id": "svc", "remote": remote, "profile": {"forge": forge, "defaultBranch": "main"}}
    task = {"branch": "feature/demo", "worktree": str(tmp_path)}
    with patch("signal_harness.pull_requests.verify_prepared_task", return_value=(task, repo)):
        result = pull_request_plan(None, "run", "task", "Example feature", body, True)
    command = result["command"]
    assert command[0] == executable
    assert command[command.index("--repo") + 1] == target
    assert "--draft" in command
    assert result["executed"] is False
    if forge == "gitlab":
        assert command[command.index("--description") + 1] == body.read_text()
        assert command[command.index("--source-branch") + 1] == "feature/demo"
        assert command[command.index("--target-branch") + 1] == "main"


def test_recursive_local_ci_includes_and_cycles(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text("include:\n  - local: /ci/build.yml\n")
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci/build.yml").write_text(
        "include:\n  - local: .gitlab-ci.yml\n  - remote: https://example.test/ci.yml\n"
    )
    assert _local_ci_context(tmp_path) == (".gitlab-ci.yml", "ci/build.yml")


def test_local_ci_include_rejects_escape(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text("include: {local: ../outside.yml}\n")
    with pytest.raises(ConfigurationError, match="inside"):
        _local_ci_context(tmp_path)


def test_nested_sensitive_values_and_provider_urls_are_redacted():
    assert redact_value({"nested": [{"access_token": "opaque", "safe": "keep"}]}) == {
        "nested": [{"access_token": "[REDACTED]", "safe": "keep"}]
    }
    result = redact_text("failed https://user:opaque@host/repo token=opaque")
    assert "opaque" not in result
    assert "user:" not in result
