# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/macos-source-audit.yml"


class MacosWorkflowShellTest(unittest.TestCase):
    def test_optional_candidate_argument_is_bash_3_2_nounset_safe(self) -> None:
        script = r'''set -u
candidate_arg=""
if [[ "$1" == true ]]; then
  candidate_arg=--allow-audit-candidate
fi
set -- ${candidate_arg:+"$candidate_arg"}
printf '%s\n' "$#"
if (( $# )); then
  printf '%s\n' "$1"
fi
'''

        release = subprocess.run(
            ["/bin/bash", "-c", script, "candidate-argument-test", "false"],
            check=True,
            capture_output=True,
            text=True,
        )
        candidate = subprocess.run(
            ["/bin/bash", "-c", script, "candidate-argument-test", "true"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual("0\n", release.stdout)
        self.assertEqual("1\n--allow-audit-candidate\n", candidate.stdout)

    def test_workflow_uses_safe_argument_in_both_macos_audit_steps(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertNotIn("candidate_args", workflow)
        self.assertEqual(2, workflow.count('candidate_arg=""'))
        self.assertEqual(2, workflow.count("candidate_arg=--allow-audit-candidate"))
        self.assertEqual(2, workflow.count('${candidate_arg:+"$candidate_arg"}'))


if __name__ == "__main__":
    unittest.main()
