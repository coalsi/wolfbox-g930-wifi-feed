#!/usr/bin/env python3
"""Prove an RTSP feed is actually delivering video, with no ffmpeg needed.

    python3 rtsp_probe.py rtsp://192.168.1.1:554/264_pcm_rt/XXX.sd
    python3 rtsp_probe.py --scan 172.20.10.0/28      # find the camera on a hotspot

Does OPTIONS/DESCRIBE/SETUP(TCP interleaved)/PLAY, reads for a few seconds,
and reports bytes, H.264 keyframes (IDR) and an approximate frame rate.
--save writes the raw Annex-B stream so it can be opened later.
"""
import argparse
import ipaddress
import socket
import time
from urllib.parse import urlparse


def scan(net, port=554):
    hits = []
    for ip in ipaddress.ip_network(net).hosts():
        try:
            socket.create_connection((str(ip), port), timeout=0.4).close()
            hits.append(str(ip))
        except OSError:
            pass
    return hits


class Rtsp:
    def __init__(self, url):
        u = urlparse(url)
        self.url = url
        self.s = socket.create_connection((u.hostname, u.port or 554), timeout=5)
        self.cseq = 0
        self.session = None

    def req(self, method, url=None, headers=None):
        self.cseq += 1
        lines = [f"{method} {url or self.url} RTSP/1.0", f"CSeq: {self.cseq}", "User-Agent: rtsp_probe"]
        if self.session:
            lines.append(f"Session: {self.session}")
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        self.s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        raw = b""
        while True:
            # Interleaved RTP ($ ch len payload) can arrive ahead of the reply.
            while raw[:1] == b"$" and len(raw) >= 4:
                ln = int.from_bytes(raw[2:4], "big")
                if len(raw) < 4 + ln:
                    break
                raw = raw[4 + ln:]
            if raw[:1] != b"$" and b"\r\n\r\n" in raw:
                break
            raw += self.s.recv(4096)
        head, _, rest = raw.partition(b"\r\n\r\n")
        hdrs = {}
        status = head.split(b"\r\n")[0].decode(errors="replace")
        for line in head.split(b"\r\n")[1:]:
            k, _, v = line.decode(errors="replace").partition(":")
            hdrs[k.strip().lower()] = v.strip()
        body = rest
        n = int(hdrs.get("content-length", 0))
        while len(body) < n:
            body += self.s.recv(4096)
        self.leftover = body[n:]
        return status, hdrs, body[:n].decode(errors="replace")


def control_url(base, sdp):
    track = None
    in_video = False
    for line in sdp.splitlines():
        if line.startswith("m="):
            in_video = line.startswith("m=video")
        if in_video and line.startswith("a=control:"):
            track = line[len("a=control:"):].strip()
            break
    if not track or track == "*":
        return base
    if track.startswith("rtsp://"):
        return track
    return base.rstrip("/") + "/" + track


def probe(url, seconds, save, keepalive):
    r = Rtsp(url)
    st, _, _ = r.req("OPTIONS")
    print("OPTIONS ", st)
    st, h, sdp = r.req("DESCRIBE", headers={"Accept": "application/sdp"})
    print("DESCRIBE", st)
    if not st.split()[1].startswith("2"):
        return False
    for line in sdp.splitlines():
        if line.startswith(("m=", "a=rtpmap", "a=fmtp", "a=framerate")):
            print("   ", line[:120])
    base = h.get("content-base", url)
    st, h, _ = r.req("SETUP", control_url(base, sdp), {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
    print("SETUP   ", st)
    r.session = h.get("session", "").split(";")[0]
    st, _, _ = r.req("PLAY", base, {"Range": "npt=0.000-"})
    print("PLAY    ", st)

    out = open(save, "wb") if save else None
    buf, total, idr, frames, rtp_ts = r.leftover, 0, 0, 0, set()
    r.s.settimeout(3)
    t0 = time.time()
    end = t0 + seconds
    last = t0
    next_ka = t0 + 1
    while time.time() < end:
        if keepalive != "none" and time.time() >= next_ka:
            next_ka += 1
            if keepalive == "rtcp":  # empty Receiver Report on interleaved channel 1
                rr = bytes([0x80, 201, 0, 1]) + (0x1234ABCD).to_bytes(4, "big")
                r.s.sendall(b"$\x01" + len(rr).to_bytes(2, "big") + rr)
            else:
                r.cseq += 1
                r.s.sendall(f"GET_PARAMETER {base} RTSP/1.0\r\nCSeq: {r.cseq}\r\nSession: {r.session}\r\n\r\n".encode())
        try:
            chunk = r.s.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        last = time.time()
        while len(buf) >= 4:
            if buf[0] != 0x24:  # stray RTSP reply; skip a byte
                buf = buf[1:]
                continue
            ch, ln = buf[1], int.from_bytes(buf[2:4], "big")
            if len(buf) < 4 + ln:
                break
            pkt, buf = buf[4:4 + ln], buf[4 + ln:]
            if ch != 0 or len(pkt) < 13:
                continue
            total += len(pkt)
            cc = pkt[0] & 0x0F
            ts = int.from_bytes(pkt[4:8], "big")
            payload = pkt[12 + 4 * cc:]
            nal = payload[0] & 0x1F
            if nal == 28:  # FU-A: only count the start fragment
                start = payload[1] & 0x80
                nal = payload[1] & 0x1F
                if out:
                    if start:
                        out.write(b"\0\0\0\1" + bytes([(payload[0] & 0xE0) | nal]))
                    out.write(payload[2:])
                if not start:
                    continue
            elif out and nal < 24:
                out.write(b"\0\0\0\1" + payload)
            if nal == 5:
                idr += 1
            if nal in (1, 5):
                rtp_ts.add(ts)
    frames = len(rtp_ts)
    try:
        r.req("TEARDOWN", base)
    except OSError:
        pass
    if out:
        out.close()
    kbps = total * 8 / 1000 / seconds
    print(f"\nRESULT: {total} bytes in {seconds}s (~{kbps:.0f} kbit/s), "
          f"{frames} frames (~{frames / seconds:.1f} fps), {idr} keyframe NALs, "
          f"last data at {last - t0:.1f}s")
    return frames > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--scan", help="CIDR to scan for port 554, e.g. 172.20.10.0/28")
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--save", help="write raw H.264 here")
    ap.add_argument("--keepalive", choices=["none", "rtcp", "param"], default="rtcp",
                    help="what to send each second so the server keeps streaming")
    a = ap.parse_args()
    if a.scan:
        hits = scan(a.scan)
        print("port 554 open on:", hits or "nothing")
        return
    ok = probe(a.url, a.seconds, a.save, a.keepalive)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
