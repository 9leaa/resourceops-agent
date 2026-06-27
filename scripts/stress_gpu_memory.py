from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Create bounded GPU memory pressure with PyTorch.")
    parser.add_argument("--mb", type=int, default=512, help="GPU memory to allocate in MB.")
    parser.add_argument("--max-mb", type=int, default=1024, help="Hard safety cap in MB.")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--yes", action="store_true", help="Required confirmation.")
    args = parser.parse_args()

    if not args.yes:
        raise SystemExit("refusing to allocate GPU memory without --yes")
    if args.mb <= 0:
        raise SystemExit("--mb must be positive")
    if args.mb > args.max_mb:
        raise SystemExit(f"refusing to allocate {args.mb}MB above --max-mb={args.max_mb}")

    try:
        import torch
    except ImportError as exc:
        raise SystemExit("torch is not installed; cannot run GPU memory stress") from exc

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is not available")

    element_count = args.mb * 1024 * 1024 // 4
    print(f"allocating about {args.mb}MB on {args.device} for {args.duration}s")

    tensor = None
    try:
        tensor = torch.empty(element_count, dtype=torch.float32, device=args.device)
        tensor.fill_(1.0)
        time.sleep(max(1, args.duration))
    except KeyboardInterrupt:
        print("stopping gpu memory stress")
    finally:
        del tensor
        torch.cuda.empty_cache()

    print("gpu memory stress finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
