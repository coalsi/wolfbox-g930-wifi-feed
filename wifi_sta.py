#!/usr/bin/env python3
"""Manage the G930's saved Wi-Fi networks and the boot-time supervisor.

    python3 wifi_sta.py install          # copy files to flash + add boot hook
    python3 wifi_sta.py uninstall        # remove hook and files
    python3 wifi_sta.py status           # connection, AP state, recent log
    python3 wifi_sta.py list             # saved networks by priority
    python3 wifi_sta.py add [--priority N]   # prompts for SSID + password
    python3 wifi_sta.py remove SSID
    python3 wifi_sta.py priority SSID N

Higher priority wins when several saved networks are in range. Use
--host to reach the camera on another network (e.g. its hotspot IP).
Passwords are never stored: only the derived WPA2 key goes to the camera.
"""
import argparse
import base64
import functools
import getpass
import hashlib
import http.server
import os
import re
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from camsh import Telnet, login, put, run  # noqa: E402

D = "/oneed_cust/wifi_sta"
CONF = f"{D}/networks.conf"
HOOK_FILE = "/oneed_cust/create_soft_link.sh"
HOOK = (f"[ -f {D}/supervisor.sh ] && (nohup sh {D}/supervisor.sh "
        "</dev/null >/dev/null 2>&1 &)  # wifi_sta hook")
WPA_CLI = f"{D}/wpa_cli -p /tmp/wpa_ctrl -i wlan1"
CONF_HEADER = "ctrl_interface=/tmp/wpa_ctrl\n# Managed by wifi_sta.py. Higher priority = preferred.\n"


class Cam:
    def __init__(self, host):
        self.host = host
        self.t = Telnet(host)
        login(self.t)

    def sh(self, cmd, timeout=30, check=True):
        out, rc = run(self.t, cmd, timeout)
        if check and rc != "0":
            sys.exit(f"camera command failed (rc={rc}): {cmd}\n{out}")
        return out.strip("\n")

    def write(self, path, text):
        blob = base64.b64encode(text.encode()).decode()
        self.sh(f"umask 077; echo '{blob}' | base64 -d > {path}.new && mv {path}.new {path}")


def parse_networks(text):
    nets = []
    for block in re.findall(r"network=\{(.*?)\}", text, re.S):
        fields = dict(re.findall(r"^\s*(\w+)=(.*?)\s*$", block, re.M))
        nets.append(fields)
    return nets


def render(nets):
    out = CONF_HEADER
    for n in sorted(nets, key=lambda n: -int(n.get("priority", 0))):
        out += "network={\n" + "".join(f"    {k}={v}\n" for k, v in n.items()) + "}\n"
    return out


def ssid_name(n):
    s = n["ssid"]
    return s[1:-1] if s.startswith('"') else bytes.fromhex(s).decode(errors="replace")


def load(cam):
    return parse_networks(cam.sh(f"cat {CONF} 2>/dev/null", check=False))


def save(cam, nets):
    cam.write(CONF, render(nets))
    cam.sh(f"{WPA_CLI} reconfigure >/dev/null 2>&1", check=False)


def local_ip_for(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((host, 9))
    ip = s.getsockname()[0]
    s.close()
    return ip


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def cmd_install(cam, a):
    bindir = os.path.join(HERE, "bin")
    free = cam.sh("df /oneed_cust | tail -1").split()
    print(f"/oneed_cust free: {free[3]} KB")
    cam.sh(f"mkdir -p {D}")

    # Serve bin/ over HTTP so the camera can wget the binaries quickly.
    ip = local_ip_for(cam.host)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=bindir)
    handler.log_message = lambda *args: None
    srv = http.server.ThreadingHTTPServer((ip, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        for name in ("wpa_supplicant", "wpa_cli"):
            cam.sh(f"wget -q http://{ip}:{port}/{name} -O {D}/{name}.new", timeout=120)
            got = cam.sh(f"sha256sum {D}/{name}.new").split()[0]
            if got != sha256(os.path.join(bindir, name)):
                sys.exit(f"checksum mismatch for {name}")
            cam.sh(f"chmod 755 {D}/{name}.new && mv {D}/{name}.new {D}/{name}")
            print(f"installed {name}")
    finally:
        srv.shutdown()

    for name in ("supervisor.sh", "net_toggle_wrap.sh", "iw_wrap.sh"):
        put(cam.t, os.path.join(HERE, "camera", name), f"{D}/{name}", 30)
        cam.sh(f"sh -n {D}/{name} && chmod 755 {D}/{name}")
        print(f"installed {name}")
    if cam.sh(f"[ -f {CONF} ] && echo yes || echo no") == "no":
        cam.write(CONF, CONF_HEADER)
        print("created empty networks.conf")

    # Boot hook: one line appended to a script demo.sh sources at boot.
    if cam.sh(f"grep -c 'wifi_sta hook' {HOOK_FILE}", check=False) in ("", "0"):
        cam.sh(f"[ -f {HOOK_FILE}.orig ] || cp {HOOK_FILE} {HOOK_FILE}.orig")
        cam.sh(f"cp {HOOK_FILE}.orig /mnt/mmc/create_soft_link.sh.orig", check=False)
        hook = HOOK.replace("'", "'\\''")
        cam.sh(f"cp {HOOK_FILE} {HOOK_FILE}.new && echo '{hook}' >> {HOOK_FILE}.new && "
               f"sh -n {HOOK_FILE}.new && mv {HOOK_FILE}.new {HOOK_FILE}")
        print(f"boot hook added to {HOOK_FILE} (backup: {HOOK_FILE}.orig)")
    else:
        print("boot hook already present")
    cam.sh("sync")

    if a.start:
        cam.sh("for p in $(ps | grep '[w]ifi_sta/supervisor' | awk '{print $1}'); do kill $p; done; "
               f"nohup sh {D}/supervisor.sh </dev/null >/dev/null 2>&1 &", check=False)
        print("supervisor started")


def cmd_uninstall(cam, a):
    cam.sh(f"for p in $(ps | grep '[w]ifi_sta/supervisor' | awk '{{print $1}}'); do kill $p; done; "
           "[ -f /tmp/wifi_sta.wpa.pid ] && kill $(cat /tmp/wifi_sta.wpa.pid); "
           "[ -f /tmp/wifi_sta.udhcpc.pid ] && kill $(cat /tmp/wifi_sta.udhcpc.pid); "
           "[ -f /tmp/wifi_sta.relay.pid ] && kill $(cat /tmp/wifi_sta.relay.pid); "
           "ifconfig wlan1 down; mount | grep -q net_toggle && umount /customer/wifi/net_toggle.sh; "
           "mount | grep -q ' /customer/wifi/iw ' && umount /customer/wifi/iw; "
           "pidof hostapd >/dev/null || (nohup ap.sh start >/dev/null 2>&1 &)",
           check=False)
    cam.sh(f"[ -f {HOOK_FILE}.orig ] && mv {HOOK_FILE}.orig {HOOK_FILE}; "
           f"grep -v 'wifi_sta hook' {HOOK_FILE} > {HOOK_FILE}.new && mv {HOOK_FILE}.new {HOOK_FILE}; "
           f"rm -rf {D}; sync")
    print("uninstalled; camera is back to stock AP behaviour")


def cmd_status(cam, a):
    print(cam.sh(
        f"echo supervisor: $(ps | grep -c '[w]ifi_sta/supervisor') running; "
        "echo AP: $(pidof hostapd >/dev/null && echo on || echo off); "
        f"{WPA_CLI} status 2>/dev/null | grep -E '^(wpa_state|ssid|ip_address|freq)='; "
        "echo menu: $(nvconf get 0 Camera.Menu.WiFi); "
        "[ -f /mnt/mmc/WIFI_AP_ONLY ] && echo 'WIFI_AP_ONLY present on SD'; "
        "[ -f /mnt/mmc/WIFI_OFF ] && echo 'WIFI_OFF present on SD'; "
        "echo net_toggle wrapper: $(mount | grep -q net_toggle && echo on || echo off); "
        "echo iw wrapper: $(mount | grep -q ' /customer/wifi/iw ' && echo on || echo off); "
        "echo --- log; tail -15 /mnt/mmc/wifi_sta.log 2>/dev/null || tail -15 /tmp/wifi_sta.log", check=False))


def cmd_list(cam, a):
    nets = load(cam)
    if not nets:
        print("no saved networks")
    for n in sorted(nets, key=lambda n: -int(n.get("priority", 0))):
        print(f"  priority {n.get('priority', 0):>3}  {ssid_name(n)}")


def cmd_add(cam, a):
    ssid = input("SSID: ").strip()
    pw = getpass.getpass("Password (hidden): ")
    if not 8 <= len(pw) <= 63:
        sys.exit("WPA2 passwords are 8-63 characters")
    nets = [n for n in load(cam) if ssid_name(n) != ssid]
    nets.append({
        "ssid": ssid.encode().hex(),
        "psk": hashlib.pbkdf2_hmac("sha1", pw.encode(), ssid.encode(), 4096, 32).hex(),
        "key_mgmt": "WPA-PSK",
        "ieee80211w": "1",
        "scan_ssid": "1",
        "priority": str(a.priority),
    })
    save(cam, nets)
    print(f"saved {ssid!r} with priority {a.priority}")


def cmd_remove(cam, a):
    nets = load(cam)
    keep = [n for n in nets if ssid_name(n) != a.ssid]
    if len(keep) == len(nets):
        sys.exit(f"{a.ssid!r} is not saved")
    save(cam, keep)
    print(f"removed {a.ssid!r}")


def cmd_priority(cam, a):
    nets = load(cam)
    for n in nets:
        if ssid_name(n) == a.ssid:
            n["priority"] = str(a.value)
            save(cam, nets)
            print(f"{a.ssid!r} priority -> {a.value}")
            return
    sys.exit(f"{a.ssid!r} is not saved")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="192.168.1.1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install")
    p.add_argument("--no-start", dest="start", action="store_false", help="don't start it until next boot")
    sub.add_parser("uninstall")
    sub.add_parser("status")
    sub.add_parser("list")
    p = sub.add_parser("add")
    p.add_argument("--priority", type=int, default=10)
    p = sub.add_parser("remove")
    p.add_argument("ssid")
    p = sub.add_parser("priority")
    p.add_argument("ssid")
    p.add_argument("value", type=int)
    a = ap.parse_args()

    cam = Cam(a.host)
    globals()[f"cmd_{a.cmd}"](cam, a)


if __name__ == "__main__":
    main()
