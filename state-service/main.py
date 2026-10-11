from __future__ import annotations

from homelab_state.launcher import main


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--icon-worker":
        from homelab_state.icon_images import main as icon_worker_main
        raise SystemExit(icon_worker_main())
    if len(sys.argv) > 1 and sys.argv[1] == "--notes-worker":
        from homelab_state.notes_worker import main as notes_worker_main
        raise SystemExit(notes_worker_main())
    raise SystemExit(main())
