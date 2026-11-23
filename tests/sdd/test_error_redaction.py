# Copyright (c) 2026 Salesforce, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import subprocess
from argparse import Namespace
from unittest.mock import Mock, patch

import pytest

from signal_harness import cli, git, github
from signal_harness.errors import ResolutionError

DIAGNOSTIC = "denied https://user:url-credential@host/repo token=assignment-credential"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_git_and_github_errors_redact_diagnostics_and_command_arguments(tmp_path, stream):
    completed = subprocess.CompletedProcess(
        [], 1, DIAGNOSTIC if stream == "stdout" else "", DIAGNOSTIC if stream == "stderr" else ""
    )
    with patch("signal_harness.git._git_process", return_value=completed):
        with pytest.raises(ResolutionError) as raised:
            git._git(tmp_path, "fetch", "https://user:url-credential@host/repo")
        assert "url-credential" not in str(raised.value)
        assert "assignment-credential" not in str(raised.value)
        assert "[REDACTED]" in str(raised.value)
    with pytest.raises(ResolutionError) as raised:
        github._run_gh(["gh", "api", "example"], tmp_path, Mock(return_value=completed))
    assert "url-credential" not in str(raised.value)
    assert "assignment-credential" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


def test_cli_sanitizes_unsanitized_handler_errors(capsys):
    handler = Mock(side_effect=ResolutionError(DIAGNOSTIC))
    parser = Mock()
    parser.parse_args.return_value = Namespace(handler=handler)
    with patch("signal_harness.cli.parser", return_value=parser):
        assert cli.main([]) == 2
    output = capsys.readouterr().err
    assert "denied" in output
    assert "[REDACTED]" in output
    assert "url-credential" not in output
    assert "assignment-credential" not in output
