# Wolfbox G930 dashcam → network feeds

Makes the G930 join Wi-Fi networks (truck hotspot, home) instead of only
offering its own AP, and keeps its RTSP feeds reachable there, so you can
view the live front/rear video on any device on the same network — a head
unit, phone, or PC. Set up and verified 2026-10-31.

If you only want to *consume* the feed from another app and don't care how
the camera side works, read [FEED-HANDOFF.md](FEED-HANDOFF.md) instead — it's
a self-contained guide to finding the camera and playing its RTSP streams.

## Disclaimer

For modifying **a dashcam you own**. It relies on the G930's factory root
telnet (no password) and bind-mounts/overrides firmware scripts in RAM; the
permanent pieces live in a vendor customization partition. This is not
affiliated with or endorsed by Wolfbox. It can brick the camera, will likely
void your warranty, and you use it at your own risk. Nothing here is built to
touch a device you don't own. **Set a root password** before leaving the
camera on any shared network (see the last section).

## Feeds (from any device on the same network as the camera)

| Camera | URL | Rate |
|---|---|---|
| Front | `rtsp://<camera-ip>:554/liveRTSP/av4` | 720p H.264, 30 fps, ~4 Mbit/s |
| Rear  | `rtsp://<camera-ip>:554/liveRTSP/av5` | 720p H.264, ~25 fps, ~3.5 Mbit/s |

Both play at the same time. Players must send RTSP keepalives (VLC,
ExoPlayer and the like do); without them the server stops after 6 s.
The web CGI is on port 80 of the same address, e.g.
`http://<camera-ip>/cgi-bin/Config.cgi?action=get&property=Camera.Menu.RearStarus`.

On one network the camera settled at 192.168.4.55. On another network it gets
whatever that network's DHCP hands out; pin it there if the head unit needs
a fixed address.

## Daily use

```
python3 wifi_sta.py [--host IP] status            # link, AP, relay, recent log
python3 wifi_sta.py [--host IP] list              # saved networks by priority
python3 wifi_sta.py [--host IP] add --priority N  # prompts for SSID + password
python3 wifi_sta.py [--host IP] remove SSID
python3 wifi_sta.py [--host IP] priority SSID N
```

`--host` defaults to the camera's AP address 192.168.1.1; use its network
address once it's connected somewhere. Higher priority wins; the camera
re-scans every 60 s while on a lower-priority network and switches when a
better one appears. For example: the truck hotspot at priority 20, home
Wi-Fi at 10, so the camera prefers the truck whenever it's in range.

Files on the SD card's root act as switches:

- `WIFI_OFF` — really turn Wi-Fi off (the camera's own menu OFF no longer
  sticks while networks are saved, see below).
- `WIFI_AP_ONLY` — stay in plain AP mode, don't join anything.

Log: `wifi_sta.log` on the SD card.

## What's installed on the camera

`/oneed_cust/wifi_sta/` (flash, survives reboots and power loss):

- `wpa_supplicant`, `wpa_cli` — static ARM build of 2.11 (the stock
  firmware has no wpa_supplicant at all, so its own STA mode never worked).
  Built by `build_wpa_supplicant.sh` with Zig; copies in `bin/`.
- `supervisor.sh` — started at boot by one line appended to
  `/oneed_cust/create_soft_link.sh` (original kept as `.orig` next to it and
  on the SD card). It joins networks on `wlan1` while the stock AP uses
  `wlan0`, turns the AP off 15 s after a network is up, back on 20 s after
  none is, keeps the AP up for the first 2 min after boot, and relays
  ports 554/80 from the network address to the recorder (which only
  listens on 192.168.1.1). It also recreates a missing RTSP stream and a
  dead relay.
- `net_toggle_wrap.sh` — bind-mounted over the firmware's `net_toggle.sh`
  at boot (RAM only). The firmware switches Wi-Fi OFF about 2 min after
  nothing has joined its AP, which is always the case when the camera is
  the one joining; this drops that OFF while networks are saved.
- `iw_wrap.sh` — bind-mounted over the firmware's `iw` at boot (RAM only).
  The UI keeps its AP-info page up, and starts the idle shutdown, until
  `iw dev wlan0 station dump` shows a connected station; this reports one
  while networks are saved, so the screen is free for the mirror view.
- `networks.conf` — saved networks (SSID + derived WPA2 key, never the
  password).

At boot the supervisor gives the firmware 45 s to bring Wi-Fi up and then
starts it itself (the UI won't after a blocked shutdown). It leaves the
recorder's RTSP alone for 2 min after any Wi-Fi start: restarting RTSP
while the recorder is starting it wedges the server (fix: `rtsp 0`, wait
5 s, `rtsp 1` on `/tmp/cardv_fifo`).

`python3 wifi_sta.py uninstall` removes all of it and restores stock.

## Tools on the Mac

- `camsh.py` — run commands on the camera over its root telnet (blank
  password), or upload a file with `--put`.
- `rtsp_probe.py` — prove a feed delivers video (frames, fps, keyframes)
  without ffmpeg; `--scan CIDR` finds the camera on a network.
- `set_sta_creds.py`, `sta_test.sh` — the original bounded one-off STA
  test; superseded by the supervisor.

## Camera facts worth knowing

- SigmaStar SSC8826, ARMv7, 72 MB RAM (~20 MB available while recording),
  BusyBox, glibc 2.30. Wi-Fi is an RTL8733BU (dual band), driver `8733bu`;
  one AP + one client at a time, sharing a channel.
- Root telnet on port 23 with no password. Anything on the same network can
  take the camera over; set a root password before leaving it on a shared
  network.
- The recorder's RTSP and the web server bind only to the AP address, and
  the firmware never brings `lo` up; the supervisor fixes both.
