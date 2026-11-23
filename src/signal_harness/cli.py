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

"""Compatibility import; implementation lives in signal_harness.sdd.cli."""

import sys

from signal_harness.sdd import cli as _implementation
from signal_harness.sdd.cli import _ack_context as _ack_context
from signal_harness.sdd.cli import _approve_command as _approve_command
from signal_harness.sdd.cli import _ask as _ask
from signal_harness.sdd.cli import _cancel as _cancel
from signal_harness.sdd.cli import _claim as _claim
from signal_harness.sdd.cli import _cleanup as _cleanup
from signal_harness.sdd.cli import _clear_gate as _clear_gate
from signal_harness.sdd.cli import _compat_check as _compat_check
from signal_harness.sdd.cli import _compat_run_check as _compat_run_check
from signal_harness.sdd.cli import _compat_run_snapshot as _compat_run_snapshot
from signal_harness.sdd.cli import _compat_snapshot as _compat_snapshot
from signal_harness.sdd.cli import _compatibility_plan as _compatibility_plan
from signal_harness.sdd.cli import _context as _context
from signal_harness.sdd.cli import _delete_run as _delete_run
from signal_harness.sdd.cli import _discover as _discover
from signal_harness.sdd.cli import _engage_repository as _engage_repository
from signal_harness.sdd.cli import _external_id as _external_id
from signal_harness.sdd.cli import _freeze as _freeze
from signal_harness.sdd.cli import _init_distribution as _init_distribution
from signal_harness.sdd.cli import _init_repository as _init_repository
from signal_harness.sdd.cli import _init_standalone as _init_standalone
from signal_harness.sdd.cli import _lock_context as _lock_context
from signal_harness.sdd.cli import _migrate_author_permissions as _migrate_author_permissions
from signal_harness.sdd.cli import _next as _next
from signal_harness.sdd.cli import _notify as _notify
from signal_harness.sdd.cli import _open_pr as _open_pr
from signal_harness.sdd.cli import _pause as _pause
from signal_harness.sdd.cli import _plan as _plan
from signal_harness.sdd.cli import _pr_plan as _pr_plan
from signal_harness.sdd.cli import _prepare as _prepare
from signal_harness.sdd.cli import _print_json as _print_json
from signal_harness.sdd.cli import _program_name as _program_name
from signal_harness.sdd.cli import _publish as _publish
from signal_harness.sdd.cli import _record as _record
from signal_harness.sdd.cli import _record_cost as _record_cost
from signal_harness.sdd.cli import _record_finding as _record_finding
from signal_harness.sdd.cli import _record_handoff as _record_handoff
from signal_harness.sdd.cli import _recover as _recover
from signal_harness.sdd.cli import _release as _release
from signal_harness.sdd.cli import _repo_overrides as _repo_overrides
from signal_harness.sdd.cli import _resolve_finding as _resolve_finding
from signal_harness.sdd.cli import _resolve_human as _resolve_human
from signal_harness.sdd.cli import _resume as _resume
from signal_harness.sdd.cli import _resume_run as _resume_run
from signal_harness.sdd.cli import _rewind as _rewind
from signal_harness.sdd.cli import _route as _route
from signal_harness.sdd.cli import _sdd_plan as _sdd_plan
from signal_harness.sdd.cli import _start as _start
from signal_harness.sdd.cli import _status as _status
from signal_harness.sdd.cli import _store as _store
from signal_harness.sdd.cli import _summary as _summary
from signal_harness.sdd.cli import _tdd_evidence as _tdd_evidence
from signal_harness.sdd.cli import _tdd_record as _tdd_record
from signal_harness.sdd.cli import _validate as _validate
from signal_harness.sdd.cli import _verify as _verify
from signal_harness.sdd.cli import main as main
from signal_harness.sdd.cli import parser as parser

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
