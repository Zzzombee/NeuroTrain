from __future__ import annotations

from scripts.prepare_events import main, parse_args, prepare_events

__all__ = ["prepare_events", "main", "parse_args"]


if __name__ == "__main__":
    raise SystemExit(main())
