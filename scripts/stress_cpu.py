from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import time


def burn_until(deadline: float) -> None:
    value = 0
    while time.time() < deadline:
        value = (value * 3 + 1) % 1000003


def main() -> int:
    parser = argparse.ArgumentParser(description="Create bounded CPU pressure.")
    parser.add_argument("--duration", type=int, default=30, help="Seconds to run.")
    parser.add_argument("--workers", type=int, default=max(1, min(2, os.cpu_count() or 1)))
    args = parser.parse_args()

    deadline = time.time() + max(1, args.duration)
    workers = max(1, args.workers)
    processes = [mp.Process(target=burn_until, args=(deadline,)) for _ in range(workers)]

    print(f"starting cpu stress: workers={workers}, duration={args.duration}s")
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        print("stopping cpu stress")
        for process in processes:
            process.terminate()
        for process in processes:
            process.join()

    print("cpu stress finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
