"""Private subprocess entry point for bounded local endpoint generation."""

from .endpoint import _worker_main

if __name__ == "__main__":
    raise SystemExit(_worker_main())
