"""Packaged Notes link handoff/rebind smoke; only accepts a local test server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "state-service"))
from tests.helpers import link_directory, repoint_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--note-id")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if args.initialize:
        for name in ("notes-primary", "notes-backup"):
            link_directory(root / (name + "-link"), root / name)
        return
    if urlsplit(args.url or "").hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Only an isolated loopback server is allowed")

    def api(path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(args.url + "/api/notes" + path, body,
                          headers={"Origin": "https://home.example.test", "X-Requested-With": "homelab-dashboard", "Content-Type": "application/json"})
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def job(response):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            value = api("/jobs/" + response["job"]["id"])["job"]
            if value["status"] != "running":
                assert value["status"] == "complete", value
                return value
            time.sleep(.1)
        raise AssertionError("Notes maintenance did not complete")

    status = api("/status?refresh=1")
    assert all(status["entryLinks"].values()), status
    target = root / "notes-primary-moved"
    shutil.copytree(root / "notes-primary", target, ignore=shutil.ignore_patterns(".writer.lock"))
    repoint_directory(root / "notes-primary-link", target)
    assert api("/" + args.note_id)["note"]["title"] == "Notes smoke"
    assert Path(api("/status?refresh=1")["resolvedPaths"]["primaryDir"]) == target
    backup = root / "notes-backup-moved"
    shutil.copytree(root / "notes-backup", backup)
    repoint_directory(root / "notes-backup-link", backup)
    assert api("/status?refresh=1")["backupErrorCode"] == "backup_target_changed"
    check = job(api("/reconcile/check", {}))
    assert check["result"]["requiresRebind"] and not check["result"]["items"], check
    applied = job(api("/reconcile/apply", {"checkId": check["id"], "choices": []}))
    assert Path(applied["result"]["reboundTarget"]) == backup
    empty = root / "notes-backup-empty"
    empty.mkdir()
    repoint_directory(root / "notes-backup-link", empty)
    check = job(api("/reconcile/check", {}))
    assert not list(empty.iterdir()), "Check initialized an empty target"
    repoint_directory(root / "notes-backup-link", backup)
    stale = api("/reconcile/apply", {"checkId": check["id"], "choices": []})
    end = time.monotonic() + 20
    while time.monotonic() < end:
        result = api("/jobs/" + stale["job"]["id"])["job"]
        if result["status"] != "running":
            assert result["error"] == "check_stale", result
            break
        time.sleep(.1)
    else:
        raise AssertionError("Stale check job hung")
    print("Packaged Notes link handoff, identical-copy rebind and stale-target gate passed.")


if __name__ == "__main__":
    main()
