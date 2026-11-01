#!/usr/bin/env python3
"""Run shell commands on the Wolfbox G930 over its root telnet (blank password).

    python3 camsh.py 'cat /proc/version' 'ps'
    python3 camsh.py --put local_file /tmp/remote_file
    python3 camsh.py --host 172.20.10.5 'ifconfig wlan0'

Each command's output is printed between markers, so nothing depends on
fixed sleeps. Telnet option negotiation is refused (we only want a dumb
byte pipe), which BusyBox telnetd accepts.
"""
import argparse
import base64
import socket
import sys
import time

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240


class Telnet:
    def __init__(self, host, port=23, timeout=10):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""

    def _recv(self, timeout):
        self.s.settimeout(timeout)
        try:
            data = self.s.recv(4096)
        except socket.timeout:
            return False
        if not data:
            raise EOFError("connection closed")
        out, i = bytearray(), 0
        while i < len(data):
            b = data[i]
            if b == IAC and i + 1 < len(data):
                cmd = data[i + 1]
                if cmd in (DO, DONT, WILL, WONT) and i + 2 < len(data):
                    opt = data[i + 2]
                    reply = WONT if cmd in (DO, DONT) else DONT
                    self.s.sendall(bytes([IAC, reply, opt]))
                    i += 3
                    continue
                if cmd == SB:
                    end = data.find(bytes([IAC, SE]), i)
                    i = len(data) if end < 0 else end + 2
                    continue
                i += 2
                continue
            out.append(b)
            i += 1
        self.buf += bytes(out)
        return True

    def read_until(self, token, timeout):
        deadline = time.time() + timeout
        while token not in self.buf:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"waiting for {token!r}; got {self.buf[-300:]!r}")
            self._recv(min(left, 1.0))
        head, _, self.buf = self.buf.partition(token)
        return head

    def read_any(self, wait):
        deadline = time.time() + wait
        while time.time() < deadline:
            self._recv(0.3)
        data, self.buf = self.buf, b""
        return data

    def write(self, text):
        self.s.sendall(text.encode())


def login(t):
    t.read_until(b"login:", 15)
    t.write("root\r\n")
    # Blank password: some builds still ask, some go straight to the shell.
    deadline = time.time() + 10
    while time.time() < deadline:
        seen = t.buf
        if b"assword" in seen:
            t.buf = b""
            t.write("\r\n")
        elif b"# " in seen:
            break
        t._recv(0.5)
    t.write("stty -echo 2>/dev/null; PS1=''; export PS1\r\n")
    t.read_any(1.0)


def run(t, cmd, timeout):
    # Split with "" so the echoed command line never contains the marker itself.
    tag = int(time.time() * 1000)
    marker = f"__END_{tag}__"
    # Marker on its own line so commands ending in "&" stay valid.
    t.write(f'{cmd}\r\necho __END_""{tag}__$?\r\n')
    out = t.read_until(marker.encode(), timeout)
    rc = t.read_until(b"\n", 5).strip()
    return out.decode(errors="replace").replace("\r", ""), rc.decode(errors="replace")


def put(t, local, remote, timeout):
    data = base64.b64encode(open(local, "rb").read()).decode()
    run(t, f"rm -f {remote}.b64", timeout)
    for i in range(0, len(data), 512):
        run(t, f"echo -n '{data[i:i + 512]}' >> {remote}.b64", timeout)
    out, rc = run(t, f"base64 -d {remote}.b64 > {remote} && rm {remote}.b64", timeout)
    if rc != "0":
        sys.exit(f"base64 decode failed on device: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.1")
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"))
    ap.add_argument("cmds", nargs="*")
    a = ap.parse_args()

    t = Telnet(a.host)
    login(t)
    if a.put:
        put(t, a.put[0], a.put[1], a.timeout)
        print(f"uploaded {a.put[0]} -> {a.put[1]}")
    for cmd in a.cmds:
        out, rc = run(t, cmd, a.timeout)
        print(f"### {cmd}  [rc={rc}]")
        print(out.strip("\n"))


if __name__ == "__main__":
    main()
