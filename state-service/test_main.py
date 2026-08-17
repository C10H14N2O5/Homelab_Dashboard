from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402
from homelab_state import launcher  # noqa: E402


class MainEntryPointTests(unittest.TestCase):
    def test_main_exposes_launcher_entrypoint(self) -> None:
        self.assertIs(main.main, launcher.main)


if __name__ == "__main__":
    unittest.main()
