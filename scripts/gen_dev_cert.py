"""
Generate a self-signed TLS certificate for local HTTPS development.

Outputs:
  certs/cert.pem  – certificate
  certs/key.pem   – private key  (NOT for production use)

Usage:
  python scripts/gen_dev_cert.py
"""
from __future__ import annotations
import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERTS_DIR = Path(__file__).parent.parent / "certs"
KEY_PATH = CERTS_DIR / "key.pem"
CERT_PATH = CERTS_DIR / "cert.pem"


def generate():
    CERTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Private key ──────────────────────────────────────────────
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_PATH.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    # ── Certificate ──────────────────────────────────────────────
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "VN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "HCM"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Ho Chi Minh City"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AISEP Dev"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"[gen_dev_cert] Certificate : {CERT_PATH}")
    print(f"[gen_dev_cert] Private key : {KEY_PATH}")
    print(f"[gen_dev_cert] Valid until : {cert.not_valid_after_utc.date()}")
    print()
    print("To trust in Windows (run as Admin):")
    print(
        f'  Import-Certificate -FilePath "{CERT_PATH.resolve()}" -CertStoreLocation Cert:\\LocalMachine\\Root')


if __name__ == "__main__":
    generate()
