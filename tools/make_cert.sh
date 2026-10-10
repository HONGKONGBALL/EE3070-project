#!/usr/bin/env bash
# Self-signed TLS certificate for the phone location page (browsers only allow
# geolocation over HTTPS). Usage: tools/make_cert.sh [PI_IP_ON_HOTSPOT]
# The phone must accept the certificate warning once. Keys stay in certs/ (git-ignored).
set -euo pipefail
cd "$(dirname "$0")/.."
IP="${1:-$(hostname -I | awk '{print $1}')}"
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout certs/cane.key -out certs/cane.crt \
  -subj "/CN=ee3070-cane" \
  -addext "subjectAltName=IP:${IP},DNS:cane.local,DNS:$(hostname).local"
chmod 600 certs/cane.key
echo "certificate for ${IP} written to certs/ - open https://${IP}:8443/phone?token=\$CANE_PHONE_TOKEN on the phone"
