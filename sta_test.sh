#!/bin/sh
# Bounded AP+STA test for the Wolfbox G930 (runs ON the camera).
#
#   sh /tmp/sta_test.sh start [HOLD_SECONDS]   # default 600
#   sh /tmp/sta_test.sh stop
#
# Joins the network in /tmp/wpa_test.conf on wlan1 and leaves the camera's
# own AP (wlan0, hostapd) alone. After HOLD seconds a watchdog tears STA
# down; if the AP didn't survive, it reboots. nvconf is never touched, so a
# reboot or power-cycle always comes back in plain AP mode.
CONF=/tmp/wpa_test.conf
CTRL=/tmp/wpa_ctrl
LOG=/mnt/mmc/sta_test.log
IW=/customer/wifi/iw

log() { echo "$(date +%T) $*" >> "$LOG"; }

ap_ok() {
    pidof hostapd >/dev/null && $IW dev wlan0 info 2>/dev/null | grep -q "type AP"
}

snapshot() {
    log "wlan0: $($IW dev wlan0 info | grep -E 'type|channel' | tr -s ' \t\n' ' ')"
    log "wlan1: $($IW dev wlan1 link | grep -E 'Connected|SSID|freq|signal' | tr -s ' \t\n' ' ')"
    log "ip: $(ifconfig wlan1 | grep 'inet addr' | tr -s ' ')"
    log "mem: $(grep MemAvailable /proc/meminfo | tr -s ' ') wpa_rss=$(grep VmRSS /proc/$(cat /tmp/wpa_test.pid 2>/dev/null)/status 2>/dev/null | tr -s ' \t' ' ') cardv=$(pidof cardv) hostapd=$(pidof hostapd)"
}

stop() {
    log "=== teardown"
    rm -f /tmp/sta_test.running
    killall udhcpc 2>/dev/null
    [ -f /tmp/wpa_test.pid ] && kill "$(cat /tmp/wpa_test.pid)" 2>/dev/null
    rm -f /tmp/wpa_test.pid
    sleep 2
    ifconfig wlan1 0.0.0.0 down
    if ap_ok; then
        log "AP ok, done"
    else
        log "AP gone -> reboot"
        sync
        reboot
    fi
}

start() {
    HOLD=${1:-600}
    [ -f "$CONF" ] || { echo "no $CONF"; exit 1; }
    touch /tmp/sta_test.running
    log "=== start, hold ${HOLD}s"
    (sleep "$HOLD"; [ -f /tmp/sta_test.running ] && sh /tmp/sta_test.sh stop) &

    ifconfig wlan1 up
    /tmp/wpa_supplicant -d -i wlan1 -D nl80211 -c "$CONF" -C "$CTRL" > /mnt/mmc/wpa_debug.log 2>&1 &
    echo $! > /tmp/wpa_test.pid

    i=0
    while [ $i -lt 40 ]; do
        state=$(/tmp/wpa_cli -p "$CTRL" -i wlan1 status 2>/dev/null | grep wpa_state)
        [ "$state" = "wpa_state=COMPLETED" ] && break
        sleep 1; i=$((i + 1))
    done
    log "after ${i}s: $state"
    snapshot

    if [ "$state" = "wpa_state=COMPLETED" ]; then
        udhcpc -i wlan1 -n -q -t 5 -T 3 -s /etc/init.d/udhcpc.script >> "$LOG" 2>&1
        log "udhcpc rc=$?"
    fi
    snapshot

    while [ -f /tmp/sta_test.running ]; do
        sleep 15
        [ -f /tmp/sta_test.running ] && snapshot
    done
}

case "$1" in
    start) start "$2" ;;
    stop) stop ;;
    *) echo "usage: $0 start [HOLD]|stop"; exit 1 ;;
esac
