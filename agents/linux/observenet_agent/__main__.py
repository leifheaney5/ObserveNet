from __future__ import annotations

import asyncio
import logging
import os

from observenet_agent.agent import Agent, Settings


def main() -> None:
    logging.basicConfig(level=os.environ.get("OBSERVENET_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")  # fmt: skip
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(Agent(Settings()).run())


if __name__ == "__main__":
    main()
