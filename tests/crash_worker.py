"""A worker process for the crash test: crash_worker.py <address> <task queue> <answers JSON>."""

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from gauntlet.worker import build_worker, connect


class SlowAgent:
    """Answers from a fixed map, slowly enough that a kill lands mid-call."""

    def __init__(self, answers: dict[str, str]) -> None:
        self._answers = answers

    def answer(self, prompt: str, schema: str) -> str:
        time.sleep(0.5)
        print("answered", flush=True)
        return self._answers[prompt]


async def main(address: str, task_queue: str, answers: dict[str, str]) -> None:
    client = await connect(address)
    with ThreadPoolExecutor() as executor:
        await build_worker(client, {"slow": SlowAgent(answers)}, executor, task_queue).run()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], json.loads(sys.argv[3])))
