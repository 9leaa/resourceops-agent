from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Create bounded memory pressure.")
    parser.add_argument("--mb", type=int, default=256, help="Memory to allocate in MB.")
    parser.add_argument("--max-mb", type=int, default=1024, help="Hard safety cap in MB.")
    parser.add_argument("--duration", type=int, default=30, help="Seconds to hold memory.")
    parser.add_argument("--chunk-mb", type=int, default=16)
    args = parser.parse_args()

    if args.mb <= 0:
        raise SystemExit("--mb must be positive")
    if args.mb > args.max_mb:
        raise SystemExit(f"refusing to allocate {args.mb}MB above --max-mb={args.max_mb}")

    chunk_mb = max(1, args.chunk_mb)
    chunks = []
    allocated = 0

    print(f"starting memory stress: target={args.mb}MB, duration={args.duration}s")
    try:
        while allocated < args.mb:
            size = min(chunk_mb, args.mb - allocated)
            chunks.append(bytearray(size * 1024 * 1024))
            allocated += size
            print(f"allocated={allocated}MB")
            time.sleep(0.05)

        time.sleep(max(1, args.duration))
    except MemoryError:
        print(f"memory allocation failed after {allocated}MB")
        return 1
    except KeyboardInterrupt:
        print("stopping memory stress")
    finally:
        chunks.clear()

    print("memory stress finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
