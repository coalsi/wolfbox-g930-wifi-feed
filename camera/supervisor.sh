#!/bin/sh
# Wolfbox G930 Wi-Fi supervisor (runs ON the camera, started at boot from
# /oneed_cust/create_soft_link.sh).
#
# wlan1 joins the best saved network in networks.conf (highest priority
# wins). While on a lower-priority one it re-scans every RESCAN seconds so
# a better network that comes up later (the truck's hotspot) takes over. The camera's own AP on wlan0 is turned off once a saved network has
# been up for UP_STABLE seconds, and back on after LOST_GRACE seconds with
# none. Ways back in:
#   - the AP is always kept up for the first BOOT_AP_GRACE seconds after boot
#   - a file named WIFI_AP_ONLY in the SD card's root forces plain AP mode
#
# The firmware's own idle timer switches Wi-Fi OFF when nothing has joined
# the AP; net_toggle_wrap.sh drops that while networks are saved, and the
# supervisor turns Wi-Fi back on if the menu is OFF anyway. To really turn
# Wi-Fi off, put a file named WIFI_OFF in the SD card's root.
#
# The recorder's RTSP server (554) and the web CGI (80) only listen on the
# AP address, so while online a tcpsvd relay forwards the same ports on the
# STA address to it. That needs loopback up, which the firmware never does.
D=/oneed_cust/wifi_sta
CTRL=/tmp/wpa_ctrl
LOG=/tmp/wifi_sta.log
IW=/customer/wifi/iw
WPA_PID=/tmp/wifi_sta.wpa.pid
NT=/customer/wifi/net_toggle.sh
NT_ORIG=/tmp/net_toggle.orig.sh
DHCP_PID=/tmp/wifi_sta.udhcpc.pid
RELAY_PID=/tmp/wifi_sta.relay.pid
RELAY_IP=/tmp/wifi_sta.relay.ip
AP_IP=$(nvconf get 1 wireless.ap.ipaddr 2>/dev/null)
AP_IP=${AP_IP:-192.168.1.1}
RELAY_PORTS="554 80"
CARDV_MSG=/bootconfig/bin/cardv_msg
HEARTBEAT=10

export PATH=/bin:/sbin:/usr/bin:/usr/sbin:/customer/wifi
export WIFI_MODULE=8733bu

AP_WHEN_CONNECTED=0
BOOT_AP_GRACE=120
UP_STABLE=15
LOST_GRACE=20
SCAN_INTERVAL=15
RESCAN=60
RTSP_CHECK=60
BAND=5G
[ -f $D/settings ] && . $D/settings

log() {
    echo "$(date +%T) $*" >> $LOG
    [ "$(wc -c < $LOG)" -gt 32768 ] && mv $LOG $LOG.1
    return 0
}

uptime_s() { cut -d. -f1 /proc/uptime; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
ap_up() { pidof hostapd >/dev/null; }
wifi_menu() { nvconf get 0 Camera.Menu.WiFi; }
has_nets() { grep -q '^network=' $D/networks.conf 2>/dev/null; }

# Highest priority in networks.conf, and the priority of the given ssid (hex).
top_priority() { sed -n 's/^ *priority=//p' $D/networks.conf | sort -n | tail -1; }
ssid_priority() {
    awk -v want="$1" '/^network=/{p=0;s=""} /^ *ssid=/{s=$1; sub("ssid=","",s)}
        /^ *priority=/{p=$1; sub("priority=","",p)} /^}/{if (s==want) print p+0}' $D/networks.conf
}

# The recorder sometimes drops one of its RTSP streams. If a stream answers
# 404 while nobody is watching, have the recorder recreate them (RTSP_CHECK s).
# The recorder tears down RTSP streams (the non-displayed one first) unless
# it gets the same periodic "stream active" message the Wolfbox app sends;
# without this the front feed dies after ~90 s even with a client watching.
heartbeat() {
    [ $(( $(uptime_s) - ${hb_at:-0} )) -lt $HEARTBEAT ] && return
    hb_at=$(uptime_s)
    [ "$(cat /tmp/rec_status 2>/dev/null)" ] && $CARDV_MSG 0x5901 >/dev/null 2>&1
}

rtsp_check() {
    [ "$(uptime_s)" -lt "${rtsp_quiet_until:-0}" ] && return
    [ $(( $(uptime_s) - ${rtsp_at:-0} )) -lt $RTSP_CHECK ] && return
    rtsp_at=$(uptime_s)
    for s in av4 av5; do
        r=$(printf "DESCRIBE rtsp://$AP_IP/liveRTSP/$s RTSP/1.0\r\nCSeq: 1\r\n\r\n" | nc -w 3 $AP_IP 554 2>/dev/null | head -1)
        case "$r" in
            *200*) ;;
            *) log "rtsp $s not served ($r); recreating RTSP sessions"
               echo rtsp 0 > /tmp/cardv_fifo; sleep 5; echo rtsp 1 > /tmp/cardv_fifo
               rtsp_quiet_until=$(( $(uptime_s) + 120 ))
               return ;;
        esac
    done
}

# Ask wpa_supplicant to look again when a better saved network may be around.
maybe_rescan() {
    [ $(( $(uptime_s) - ${rescan_at:-0} )) -lt $RESCAN ] && return
    rescan_at=$(uptime_s)
    cur=$(ssid_priority "$(printf %s "$ssid" | od -An -tx1 | tr -d ' \n')")
    [ "${cur:-0}" -lt "$(top_priority)" ] || return
    $D/wpa_cli -p $CTRL -i wlan1 scan >/dev/null 2>&1
}

# The firmware looks for a station on wlan0 to decide Wi-Fi is in use;
# iw_wrap.sh reports one so the UI's AP-info page closes and stays closed.
wrap_iw() {
    mount | grep -q " /customer/wifi/iw " && return
    cp /customer/wifi/iw /tmp/iw.orig && chmod 755 /tmp/iw.orig $D/iw_wrap.sh && \
        mount --bind $D/iw_wrap.sh /customer/wifi/iw && log "iw wrapper on" && return
    log "iw wrapper FAILED"
}

wrap_net_toggle() {
    mount | grep -q net_toggle && return
    cp $NT $NT_ORIG && chmod 755 $D/net_toggle_wrap.sh && \
        mount --bind $D/net_toggle_wrap.sh $NT && log "net_toggle wrapper on" && return
    log "net_toggle wrapper FAILED"
    NT_ORIG=$NT
}

wifi_on() {
    [ $(( $(uptime_s) - ${wifi_on_at:-0} )) -lt 60 ] && return
    wifi_on_at=$(uptime_s)
    log "turning Wi-Fi on ($BAND): $1"
    nohup sh $NT_ORIG $BAND >> $LOG 2>&1 &
    rtsp_quiet_until=$(( $(uptime_s) + 120 ))
}

# Log to the SD card once it's mounted (touching /mnt/mmc before the mount
# would write under the mount point, where it's hidden later).
use_sd_log() {
    [ "$LOG" = /mnt/mmc/wifi_sta.log ] && return
    mount | grep -q " /mnt/mmc " || return
    cat /tmp/wifi_sta.log >> /mnt/mmc/wifi_sta.log 2>/dev/null && rm -f /tmp/wifi_sta.log
    LOG=/mnt/mmc/wifi_sta.log
}

ap_on() {
    # ap.sh takes a few seconds; don't launch a second one meanwhile.
    [ $(( $(uptime_s) - ${ap_on_at:-0} )) -lt 30 ] && return
    ap_on_at=$(uptime_s)
    log "AP on"
    nohup ap.sh start >> $LOG 2>&1 &
}

ap_off() {
    log "AP off"
    killall hostapd udhcpd 2>/dev/null
}

relay_stop() {
    [ -f $RELAY_PID ] && for p in $(cat $RELAY_PID); do kill "$p" 2>/dev/null; done
    rm -f $RELAY_PID $RELAY_IP
}

# A port is reachable on $ip when something listens on 0.0.0.0:port (the
# recorder sometimes binds there itself, when wlan0 had no address as it
# started) or on $ip:port (our own tcpsvd).
port_served() {   # $1=ip $2=port
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "(^0\.0\.0\.0:$2\$|^$1:$2\$)"
}

# Relay is fine only if it was started for this IP and every port it should
# cover is actually being served (by our tcpsvd or by the recorder directly).
relay_ok() {
    [ "$1" = "$(cat $RELAY_IP 2>/dev/null)" ] || return 1
    for port in $RELAY_PORTS; do port_served "$1" "$port" || return 1; done
}

relay_start() {
    relay_stop
    # The AP address must stay on wlan0 even with hostapd stopped.
    ifconfig wlan0 | grep -q "addr:$AP_IP " || ifconfig wlan0 $AP_IP netmask 255.255.255.0
    started=
    for port in $RELAY_PORTS; do
        # Skip a port the recorder already serves on every interface.
        port_served 0.0.0.0 "$port" && continue
        tcpsvd -c 8 "$1" "$port" nc $AP_IP "$port" </dev/null >/dev/null 2>&1 &
        echo $! >> $RELAY_PID
        started="$started $port"
    done
    echo "$1" > $RELAY_IP
    log "relay on $1 ->$AP_IP (tcpsvd:${started:- none, served direct})"
}

dhcp_stop() {
    relay_stop
    alive $DHCP_PID && kill "$(cat $DHCP_PID)"
    rm -f $DHCP_PID
    ifconfig wlan1 0.0.0.0 2>/dev/null
}

sta_stop() {
    dhcp_stop
    alive $WPA_PID && kill "$(cat $WPA_PID)"
    rm -f $WPA_PID
    ifconfig wlan1 down 2>/dev/null
}

sta_start() {
    ifconfig wlan1 up || return
    $D/wpa_supplicant -B -i wlan1 -D nl80211 -c $D/networks.conf -P $WPA_PID >> $LOG 2>&1
    sleep 1
    $D/wpa_cli -p $CTRL -i wlan1 scan_interval $SCAN_INTERVAL >/dev/null 2>&1
    log "wpa_supplicant started"
}

ifconfig lo 127.0.0.1 up 2>/dev/null
relay_stop   # a restart must not trust a previous instance's relay

# Give the stock firmware a chance to bring Wi-Fi up (the UI normally does),
# then do it ourselves: after a blocked idle-OFF the UI boots believing
# Wi-Fi is off and never starts it.
i=0
while [ $i -lt 45 ]; do
    [ -d /sys/class/net/wlan1 ] && ap_up && break
    alive $WPA_PID && break          # restarted with STA already running
    [ "$(wifi_menu)" = "OFF" ] && break
    sleep 1; i=$((i + 1))
done
use_sd_log
log "=== supervisor start after ${i}s (menu=$(wifi_menu), uptime $(uptime_s)s)"
wrap_net_toggle
wrap_iw
if ! ap_up && ! alive $WPA_PID && [ ! -f /mnt/mmc/WIFI_OFF ] && \
   { [ "$(wifi_menu)" != "OFF" ] || has_nets; }; then
    wifi_on "firmware didn't start it at boot"
    i=0
    while [ $i -lt 40 ] && ! ap_up; do sleep 1; i=$((i + 1)); done
    log "AP up after ${i}s"
fi
rtsp_quiet_until=$(( $(uptime_s) + 120 ))

up=0; down=0; bssid=; ssid=; last=
while true; do
    sleep 5
    use_sd_log
    heartbeat
    ap_up && ap_on_at=

    if [ -f /mnt/mmc/WIFI_OFF ]; then
        if alive $WPA_PID || ap_up; then
            log "WIFI_OFF on SD card: Wi-Fi off"
            sta_stop
            sh $NT_ORIG OFF >> $LOG 2>&1
        fi
        up=0; down=0; ssid=; bssid=; last=
        continue
    fi

    if [ "$(wifi_menu)" = "OFF" ]; then
        if has_nets; then
            wifi_on "menu is OFF with saved networks"
        elif alive $WPA_PID; then
            log "STA off (menu OFF, no saved networks)"
            sta_stop
        fi
        up=0; down=0; ssid=; bssid=; last=
        continue
    fi

    if [ -f /mnt/mmc/WIFI_AP_ONLY ]; then
        if alive $WPA_PID; then
            log "WIFI_AP_ONLY on SD card: STA off"
            sta_stop
        fi
        ap_up || ap_on
        up=0; down=0; ssid=; bssid=; last=
        continue
    fi

    # STA is held off for a while after it had to make way for the AP.
    if [ "$(uptime_s)" -lt "${sta_hold_until:-0}" ]; then
        continue
    fi
    alive $WPA_PID || sta_start

    st=$($D/wpa_cli -p $CTRL -i wlan1 status 2>/dev/null)
    state=$(echo "$st" | sed -n 's/^wpa_state=//p')
    now=$(echo "$st" | sed -n 's/^bssid=//p')

    [ "$state" != "$last" ] && log "wpa_state $last -> $state" && last=$state

    if [ "$state" = "COMPLETED" ]; then
        name=$(echo "$st" | sed -n 's/^ssid=//p')
        if [ "$name" != "$ssid" ] || ! alive $DHCP_PID; then
            ssid=$name; bssid=$now
            log "joined $ssid ($bssid, $(echo "$st" | sed -n 's/^freq=//p') MHz)"
            dhcp_stop
            udhcpc -i wlan1 -f -t 4 -T 3 -A 5 -p $DHCP_PID -s /etc/init.d/udhcpc.script >/dev/null 2>&1 &
        elif [ "$now" != "$bssid" ]; then
            log "roamed to $now"
            bssid=$now
        fi
    elif [ -n "$ssid" ]; then
        log "lost $ssid ($state)"
        ssid=; bssid=
        dhcp_stop
    fi

    # Online = associated, has an IP, and the gateway answers (2 misses allowed).
    ip=$(ifconfig wlan1 2>/dev/null | sed -n 's/.*inet addr:\([0-9.]*\).*/\1/p')
    gw=$(route -n | awk '$1 == "0.0.0.0" && $8 == "wlan1" {print $2; exit}')
    if [ -n "$ip" ] && [ -n "$gw" ] && ping -c 1 -W 2 "$gw" >/dev/null 2>&1; then
        miss=0
    else
        miss=$((${miss:-0} + 1))
        [ -n "$ip" ] && log "gateway ${gw:-none} not answering ($miss)"
    fi

    if [ "$state" = "COMPLETED" ] && [ -n "$ip" ] && [ $miss -le 2 ]; then
        [ $up -eq 0 ] && log "online at $ip via $gw"
        up=$((up + 5)); down=0
        relay_ok "$ip" || relay_start "$ip"
        maybe_rescan
        rtsp_check
        if [ "$AP_WHEN_CONNECTED" = "0" ] && [ $up -ge $UP_STABLE ] && \
           [ "$(uptime_s)" -ge $BOOT_AP_GRACE ] && ap_up; then
            ap_off
        fi
    else
        down=$((down + 5)); up=0
        if [ $down -ge $LOST_GRACE ] && ! ap_up; then
            if [ -n "$ap_on_at" ] && [ $(( $(uptime_s) - ap_on_at )) -ge 30 ]; then
                # The last AP start didn't take: the radio may be pinned to a
                # channel the AP can't use. Free it and hold STA off for 2 min.
                log "AP didn't come up; stopping STA for 120s"
                sta_stop
                ssid=; bssid=; last=
                sta_hold_until=$(( $(uptime_s) + 120 ))
                ap_on_at=
                sleep 2
            fi
            ap_on
        fi
    fi
done
