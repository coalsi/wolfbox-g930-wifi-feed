#!/usr/bin/env python3
"""Write the Wi-Fi network for the camera's STA test to /tmp/wpa_test.conf.

    python3 set_sta_creds.py [--host 192.168.1.1]

Prompts for the SSID and password. Only the derived WPA2 key (PBKDF2, the
same thing wpa_passphrase prints) is sent to the camera, into RAM (/tmp),
so it's gone after a reboot. Nothing is saved on the Mac.
"""
import argparse
import base64
import getpass
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camsh import Telnet, login, run  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.1")
    a = ap.parse_args()

    ssid = input("Hotspot name (SSID): ").strip()
    pw = getpass.getpass("Hotspot password (hidden): ")
    if not 8 <= len(pw) <= 63:
        sys.exit("WPA2 passwords are 8-63 characters")
    psk = hashlib.pbkdf2_hmac("sha1", pw.encode(), ssid.encode(), 4096, 32).hex()

    conf = (
        "network={\n"
        f"    ssid={ssid.encode().hex()}\n"
        f"    psk={psk}\n"
        "    key_mgmt=WPA-PSK\n"
        "    ieee80211w=1\n"
        "    scan_ssid=1\n"
        "}\n"
    )
    t = Telnet(a.host)
    login(t)
    blob = base64.b64encode(conf.encode()).decode()
    out, rc = run(t, f"umask 077; echo '{blob}' | base64 -d > /tmp/wpa_test.conf && wc -c < /tmp/wpa_test.conf", 15)
    if rc != "0":
        sys.exit(f"failed: {out}")
    print(f"Wrote /tmp/wpa_test.conf on the camera for SSID {ssid!r}.")


if __name__ == "__main__":
    main()
