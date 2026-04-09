"""
start_https.py — Convenience HTTPS launcher for local dev.

Reads SSL_KEYFILE / SSL_CERTFILE / SERVER_PORT from .env (or environment).
Falls back to certs/key.pem + certs/cert.pem and port 8443 if not set.

Usage:
  python start_https.py              # HTTPS on port 8443 (default dev certs)
  python start_https.py --port 443   # override port

Run gen_dev_cert first if you don't have certs yet:
  python scripts/gen_dev_cert.py
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
from pathlib import Path

# Load .env before importing settings
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import uvicorn

# ── Defaults ────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent
_DEFAULT_KEY = str(_REPO_ROOT / "certs" / "key.pem")
_DEFAULT_CERT = str(_REPO_ROOT / "certs" / "cert.pem")
_DEFAULT_PORT = 8443
_DEFAULT_HOST = "0.0.0.0"

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("start_https")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start AISEP-AI in HTTPS mode")
    parser.add_argument(
        "--host", default=os.getenv("SERVER_HOST", _DEFAULT_HOST))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("SERVER_PORT", str(_DEFAULT_PORT))))
    parser.add_argument(
        "--keyfile",  default=os.getenv("SSL_KEYFILE",  _DEFAULT_KEY))
    parser.add_argument(
        "--certfile", default=os.getenv("SSL_CERTFILE", _DEFAULT_CERT))
    parser.add_argument(
        "--ca-certs", default=os.getenv("SSL_CA_CERTS", ""), dest="ca_certs")
    parser.add_argument("--reload", action="store_true", default=True)
    args = parser.parse_args()

    # Validate paths
    for label, path in [("keyfile", args.keyfile), ("certfile", args.certfile)]:
        if not Path(path).exists():
            log.error("TLS %s not found: %s", label, path)
            log.error("Run:  python scripts/gen_dev_cert.py")
            sys.exit(1)

    ssl_kwargs: dict = dict(
        ssl_keyfile=args.keyfile,
        ssl_certfile=args.certfile,
    )
    if args.ca_certs:
        ssl_kwargs["ssl_ca_certs"] = args.ca_certs

    log.info("════════════════════════════════════════")
    log.info("  AISEP-AI  —  HTTPS mode")
    log.info("  https://%s:%d", args.host if args.host !=
             "0.0.0.0" else "localhost", args.port)
    log.info("  cert  : %s", args.certfile)
    log.info("  key   : %s", args.keyfile)
    log.info("════════════════════════════════════════")

    uvicorn.run(
        "src.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
