"""Create the Pi phone link and serve its certificate profile during setup."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import secrets
import shutil
import socket
import subprocess
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Final

from cane.web.pairing import LAN_NETWORKS, PHONE_PORT, PhonePair

PROFILE_PORT: Final = 8081


def lan_address() -> str:
    """Select the local address on the Pi's default network interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("192.0.2.1", 80))
        return str(route.getsockname()[0])


def certificates(scratch: Path, address: IPv4Address) -> None:
    """Issue one server certificate and discard the signing key."""
    key = scratch / "root.key"
    root = scratch / "root.pem"
    leaf = scratch / "server.key"
    request = scratch / "server.csr"
    extensions = scratch / "extensions.cnf"
    extensions.write_text(
        "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=root\n"
        "[dn]\nCN=EE3070 Pi 5 Location\n"
        "[root]\nbasicConstraints=critical,CA:TRUE,pathlen:0\n"
        "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n"
        "[leaf]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        f"extendedKeyUsage=serverAuth\nsubjectAltName=IP:{address}\n",
        encoding="utf-8",
    )
    commands = (
        ["req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "365",
         "-config", str(extensions), "-keyout", str(key), "-out", str(root)],
        ["req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={address}",
         "-keyout", str(leaf), "-out", str(request)],
        ["x509", "-req", "-sha256", "-days", "90", "-in", str(request), "-CA", str(root),
         "-CAkey", str(key), "-set_serial", f"0x{secrets.token_hex(16)}",
         "-extfile", str(extensions), "-extensions", "leaf", "-out", str(scratch / "server.crt")],
    )
    for command in commands:
        subprocess.run(["openssl", *command], check=True, capture_output=True)
    der = subprocess.check_output(["openssl", "x509", "-in", str(root), "-outform", "DER"])
    (scratch / "root.cer").write_bytes(der)


def create_pair(directory: Path, address: IPv4Address) -> PhonePair:
    """Reuse the phone identity while its bound IP and leaf are valid."""
    if directory.exists():
        pairing = PhonePair.load(directory)
        if pairing.address != address:
            raise ValueError("Pi address changed. Use a new --directory to issue a matching certificate.")
        subprocess.run(
            ["openssl", "x509", "-checkend", "60", "-noout", "-in", str(directory / "server.crt")],
            check=True, capture_output=True,
        )
        return pairing
    directory.parent.mkdir(parents=True, exist_ok=True)
    pairing = PhonePair(address=address, token=secrets.token_urlsafe(32))
    with TemporaryDirectory(dir=directory.parent) as temporary:
        scratch = Path(temporary)
        certificates(scratch, address)
        staged = scratch / "pair"
        staged.mkdir(mode=0o700)
        for name in ("root.cer", "server.key", "server.crt"):
            (scratch / name).rename(staged / name)
        identity = staged / "pair.json"
        identity.write_text(pairing.model_dump_json(), encoding="utf-8")
        identity.chmod(0o600)
        staged.rename(directory)
    return pairing


def show_link(label: str, url: str) -> None:
    """Show the local link and, when installed, its terminal QR code."""
    print(f"{label}\n{url}", flush=True)
    executable = shutil.which("qrencode")
    if executable:
        subprocess.run([executable, "-t", "ANSIUTF8", url], check=False)


def share_profile(address: IPv4Address, der: bytes) -> None:
    """Offer the public root profile once on the Pi's local network."""
    root_id = "org.ee3070.pi-location"
    profile = plistlib.dumps({
        "PayloadType": "Configuration", "PayloadVersion": 1, "PayloadUUID": str(uuid.uuid4()),
        "PayloadIdentifier": root_id, "PayloadDisplayName": "EE3070 Pi 5 Location",
        "PayloadContent": [{
            "PayloadType": "com.apple.security.root", "PayloadVersion": 1,
            "PayloadUUID": str(uuid.uuid4()), "PayloadIdentifier": root_id + ".root",
            "PayloadDisplayName": "EE3070 Pi 5 Location", "PayloadCertificateFileName": "ee3070-pi.cer",
            "PayloadContent": der,
        }],
    })
    endpoint = "/" + secrets.token_urlsafe(16) + ".mobileconfig"
    delivered = False

    class ProfileHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal delivered
            if self.path != endpoint:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-apple-aspen-config")
            self.send_header("Content-Length", str(len(profile)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(profile)
            delivered = True

        def log_message(self, format: str, *args: str) -> None:
            return

    with HTTPServer((str(address), PROFILE_PORT), ProfileHandler) as server:
        server.timeout = 1.0
        show_link("在 iPhone Safari 下载此 Pi 的配置描述文件", f"http://{address}:{PROFILE_PORT}{endpoint}")
        print("根证书 SHA-256", hashlib.sha256(der).hexdigest(), flush=True)
        deadline = monotonic() + 300
        while not delivered and monotonic() < deadline:
            server.handle_request()


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the Pi 5 location and destination page")
    parser.add_argument("--address", type=IPv4Address)
    parser.add_argument("--directory", type=Path, default=Path("certs/pi-phone"))
    parser.add_argument("--profile", action="store_true", help="Serve the iPhone certificate profile once")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        address = args.address or IPv4Address(lan_address())
        if not any(address in network for network in LAN_NETWORKS):
            parser.error("use the Pi's private IPv4 address on the iPhone's network")
        pair = create_pair(args.directory, address)
        if args.profile:
            share_profile(address, (args.directory / "root.cer").read_bytes())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(f"phone setup failed ({type(exc).__name__}); check address, certificate and state directory")
    show_link("在 iPhone Safari 打开步行导航", f"https://{address}:{PHONE_PORT}/phone#token={pair.token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
