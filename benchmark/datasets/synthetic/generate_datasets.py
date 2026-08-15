#!/usr/bin/env python3
"""Regenerate the frozen v1 synthetic datasets."""

from __future__ import annotations

import argparse
from pathlib import Path

from inference_gateway.benchmark.generators import DEFAULT_SEED, generate_all


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    for base in generate_all(args.output_dir, args.seed):
        print(base)


if __name__ == "__main__":
    main()
