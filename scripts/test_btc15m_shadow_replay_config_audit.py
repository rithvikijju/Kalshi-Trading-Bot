#!/usr/bin/env python3
"""Tests for BTC15M shadow/replay config audit helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_btc15m_shadow_replay_config_audit import parse_env_setdefaults


class Btc15mShadowReplayConfigAuditTests(unittest.TestCase):
    def test_parse_env_literals_reads_assignments_and_setdefaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrapper.py"
            path.write_text(
                "\n".join(
                    [
                        "import os",
                        'os.environ["BTC15M_H02_TTL_LO"] = "10.0"',
                        'os.environ.setdefault("BTC15M_H02_TTL_HI", "12.0")',
                    ]
                ),
                encoding="utf-8",
            )

            parsed = parse_env_setdefaults(path)

        self.assertEqual(parsed["BTC15M_H02_TTL_LO"], "10.0")
        self.assertEqual(parsed["BTC15M_H02_TTL_HI"], "12.0")


if __name__ == "__main__":
    unittest.main()
