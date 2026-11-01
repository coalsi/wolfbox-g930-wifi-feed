# Wolfbox G930 dashcam — live feed integration

Handoff for an agent that will display the dashcam's video on another device
(e.g. a GT7 Android head unit) over a shared network. **The camera side is
already fully set up** — it joins Wi-Fi networks on its own and serves the
feeds. This doc is only about finding it and playing it. You do not need to
touch the camera.

## TL;DR

Two live RTSP streams, H.264, no authentication:

| Camera | URL |
|---|---|
| Front | `rtsp://<CAMERA_IP>:554/liveRTSP/av4` |
| Rear  | `rtsp://<CAMERA_IP>:554/liveRTSP/av5` |

Both can play at once (~7 Mbit/s combined). `<CAMERA_IP>` is whatever the
network's DHCP gave the camera — see **Finding the camera**. Use TCP
transport and let the player send RTSP keepalives (ExoPlayer and VLC do).
Treat the front stream as occasionally dropping and reconnect — see
**Resilience**.

## Stream specs (measured)

- **Video:** H.264 (AVC), 1280×720, Baseline/Main, `profile-level-id=64001F`,
  `packetization-mode=1`. SDP carries `sprop-parameter-sets` (SPS/PPS), so no
  out-of-band config needed.
  - Front (av4): ~30 fps, ~4 Mbit/s.
  - Rear (av5): ~25 fps, ~3.3 Mbit/s.
- **Audio:** G.711 a-law advertised as `PCMA/16000` (16 kHz, which is
  non-standard — PCMA is normally 8 kHz). Some players mishandle it. If audio
  is noisy or breaks playback, **select video only**; audio is not needed for
  a dashboard/mirror view.
- **Server:** live555. Supports `OPTIONS/DESCRIBE/SETUP/PLAY/GET_PARAMETER/
  TEARDOWN`. Both UDP (`RTP/AVP`) and TCP-interleaved (`RTP/AVP/TCP`) work.
- **No RTSP auth.** Anything on the network can read the streams.

## Finding the camera on a network

The camera is a DHCP client; its address changes per network. It does **not**
advertise mDNS/Bonjour, so you can't rely on a `.local` name. Options, best
first:

1. **DHCP reservation (recommended).** On the network's router/hotspot,
   reserve a fixed IP for the camera's Wi-Fi MAC so the URL never changes.
   Use the camera's **station (client) MAC** — the `wlan1` interface, not
   the AP. Read it off the camera with `cat /sys/class/net/wlan1/address`
   (over its root telnet), or just find it in the router's DHCP lease table
   / connected-devices list. If the head unit *is* the hotspot host, match
   that MAC in its own lease table.
2. **Scan the subnet for the RTSP port.** The camera is the host that answers
   on TCP 554 *and* 80 (its web CGI). Example (any Unix-y environment):
   ```sh
   # replace 192.168.x with the network's /24
   for n in $(seq 1 254); do
     ip=192.168.x.$n
     nc -z -w1 "$ip" 554 2>/dev/null && echo "$ip has RTSP"
   done
   ```
   Then confirm it's the dashcam with a DESCRIBE (expect `200 OK` and an
   `m=video` line):
   ```sh
   printf 'DESCRIBE rtsp://%s/liveRTSP/av4 RTSP/1.0\r\nCSeq: 1\r\n\r\n' "$ip" \
     | nc -w3 "$ip" 554
   ```
3. **Ask the router.** Its connected-devices list usually shows the camera by
   the MAC above.

The camera also keeps a web server on port 80 at the same IP; a quick identity
check that doesn't need RTSP:
```
http://<CAMERA_IP>/cgi-bin/Config.cgi?action=get&property=Camera.Menu.RearStarus
```
returns `Camera.Menu.RearStarus=ON` and `OK`.

## Implementing the client

### Android head unit — ExoPlayer / Media3 (recommended)

The GT7 is Android, so Media3's RTSP source is the straightforward path.

```kotlin
// build.gradle: implementation "androidx.media3:media3-exoplayer-rtsp:1.4.1"
val cameraIp = "192.168.x.y"            // from discovery, or your reserved IP
val front = "rtsp://$cameraIp:554/liveRTSP/av4"

val source = RtspMediaSource.Factory()
    .setForceUseRtpTcp(true)            // TCP interleaved: reliable over Wi-Fi
    .setTimeoutMs(8000)
    .createMediaSource(MediaItem.fromUri(front))

val player = ExoPlayer.Builder(context).build().apply {
    setMediaSource(source)
    prepare()
    playWhenReady = true
}
```

Notes:
- **Force TCP** (`setForceUseRtpTcp(true)`). UDP works but drops frames on a
  busy 2.4 GHz network; TCP interleaving is steadier for a dashboard.
- **Keepalive is automatic** in Media3/ExoPlayer and libVLC. Do not disable
  it — see the warning below.
- For **both feeds at once**, create two independent players/surfaces with the
  two URLs. The camera serves them simultaneously.
- Expect ~0.3–1 s latency; this is a surveillance-grade live555 stream, not
  low-latency WebRTC. Fine for a mirror/dashboard, not for anything needing
  sub-100 ms.

### Quick test from a laptop (no code)

```sh
vlc rtsp://<CAMERA_IP>:554/liveRTSP/av5           # rear
ffplay -rtsp_transport tcp rtsp://<CAMERA_IP>:554/liveRTSP/av4   # front
```

## Resilience — IMPORTANT

1. **Keepalive is mandatory.** The server **stops sending after ~6 seconds**
   if the client sends no RTSP keepalive (RTCP receiver reports or periodic
   `GET_PARAMETER`). Mainstream players do this automatically; a hand-rolled
   client MUST send one of them every 1–5 s or the video will freeze at 6 s.

2. **The front stream (av4) can drop intermittently.** The recorder sometimes
   tears down the front stream; a DESCRIBE/SETUP then returns
   `404 ... Stream Not Found`. The rear (av5) is reliable. The camera runs a
   self-heal that recreates a dropped stream within ~60 s, so the fix on the
   client side is simply: **on error or 404, retry the same URL every few
   seconds until it reconnects.** Don't treat one failure as fatal; keep the
   player's auto-reconnect/retry enabled.

   ```kotlin
   player.addListener(object : Player.Listener {
       override fun onPlayerError(e: PlaybackException) {
           player.postDelayed({ player.prepare() }, 3000)  // simple backoff
       }
   })
   ```

3. **Keep the stream open.** Continuously holding the stream open (a live
   dashboard does this) is also the condition under which it's most stable.
   Tearing down and re-opening repeatedly is what tends to leave it in a bad
   state.

## Optional: camera control (HTTP CGI, port 80)

Not required to view the feeds, but available:

- `http://<CAMERA_IP>/cgi-bin/Config.cgi?action=get&property=<Name>`
- `http://<CAMERA_IP>/cgi-bin/Config.cgi?action=set&property=<Name>&value=<V>`

Useful properties seen: `Camera.Menu.RearStarus` (rear camera on/off),
`Camera.Preview.Source.1.Camid` (`front`/`rear` — which camera the device's
own screen shows; does not affect which RTSP URLs exist). There is no auth, so
only issue `set` deliberately.

## Network assumptions

- The camera is pre-configured to join saved Wi-Fi networks by priority and
  prefers the truck network when it's in range. You just need the head unit
  and the camera on the **same** network/subnet.
- If the head unit hosts the hotspot the camera joins, give the camera a
  **DHCP reservation** (by its `wlan1` MAC — see *Finding the camera*) so its
  IP is stable and you can hard-code the URLs.
- The camera is 2.4/5 GHz capable; the network it joins must be WPA2.

## One-line checklist for bring-up

1. Camera and head unit on the same Wi-Fi network.
2. Find `<CAMERA_IP>` (reservation, port-554 scan, or router table).
3. Play `rtsp://<CAMERA_IP>:554/liveRTSP/av5` (rear) — should show video in
   a few seconds. If it freezes at ~6 s, keepalive is off.
4. Add `.../av4` (front) in a second player.
5. Enable retry-on-error so an occasional front-stream drop self-recovers.
