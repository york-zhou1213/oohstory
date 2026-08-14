from __future__ import annotations

import logging

from .main import audiobook_service


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    manager = audiobook_service()._cast_prewarm
    if not manager.worker_enabled:
        raise RuntimeError("OOHSTORY_CAST_PREWARM_WORKER must be enabled")
    manager.join()


if __name__ == "__main__":
    main()
