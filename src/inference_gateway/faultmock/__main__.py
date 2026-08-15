"""Run the fault mock as a standalone local HTTP service."""

from __future__ import annotations

import argparse

import uvicorn

from inference_gateway.faultmock import create_faultmock_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m inference_gateway.faultmock")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9401)
    args = parser.parse_args()
    uvicorn.run(create_faultmock_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
