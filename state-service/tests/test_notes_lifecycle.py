from __future__ import annotations

from dataclasses import replace
import json
import logging
from http.client import HTTPConnection
import threading
import unittest
from unittest import mock

from homelab_state import backgrounds, http_api
from tests.helpers import FIXED_TIME, TemporaryProject


class NotesStartupRecoveryTests(unittest.TestCase):
    def test_failed_primary_startup_recovers_without_restarting_other_features(self):
        project = TemporaryProject()
        self.addCleanup(project.close)
        config = replace(project.write_config(version=4, notes={
            "enabled": True, "primaryDir": "notes-primary", "backupDir": "notes-backup",
        }), listen_port=0)
        config.notes_primary_dir.mkdir()
        config.notes_backup_dir.mkdir()
        real_service = http_api.NotesService
        attempts = []

        def initially_unavailable(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise PermissionError("primary volume temporarily unavailable")
            return real_service(*args, **kwargs)

        logger = logging.getLogger("notes-startup-recovery")
        logger.addHandler(logging.NullHandler())
        with mock.patch.object(http_api, "NotesService", side_effect=initially_unavailable):
            server = http_api.StateHTTPServer(config,
                backgrounds.StateStore(config.background_dir, config.state_file, lambda: FIXED_TIME),
                logger, probe_client=mock.Mock(), bestdori_manager=mock.Mock(), bing_manager=mock.Mock())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def read(path):
                    connection = HTTPConnection(*server.server_address, timeout=5)
                    try:
                        connection.request("GET", path)
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()

                self.assertEqual(read("/healthz")[0], 200)
                self.assertFalse(read("/api/notes/status")[1]["primaryAvailable"])
                self.assertFalse(read("/api/notes/status")[1]["primaryAvailable"])
                self.assertEqual(len(attempts), 1, "requests within backoff must not reinitialize")
                server._notes_retry_at = 0.0  # Advance the retry deadline without a 15-second test sleep.
                self.assertTrue(read("/api/notes/status")[1]["primaryAvailable"])
                self.assertEqual(read("/api/notes")[0], 200)
                self.assertEqual(len(attempts), 2)
                self.assertEqual(read("/healthz")[0], 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
