#!/bin/sh
# wifi_sta: bind-mounted over /customer/wifi/iw by supervisor.sh (RAM only).
#
# The firmware decides "someone is using Wi-Fi" by looking for a station in
# `iw dev wlan0 station dump`; with the camera joining a network instead of
# serving one, that list is always empty, so the UI keeps its AP-info page
# up and starts its idle shutdown. While saved networks exist, report one
# connected station. Everything else goes to the real iw.
if [ "$1 $2 $3 $4" = "dev wlan0 station dump" ] && \
   grep -q '^network=' /oneed_cust/wifi_sta/networks.conf 2>/dev/null; then
    /tmp/iw.orig "$@" 2>/dev/null
    cat <<'EOF'
Station 02:00:5e:00:53:01 (on wlan0)
	inactive time:	120 ms
	rx bytes:	1048576
	rx packets:	8192
	tx bytes:	4194304
	tx packets:	4096
	signal:  	-45 dBm
	tx bitrate:	72.2 MBit/s
	authorized:	yes
	authenticated:	yes
	associated:	yes
EOF
    exit 0
fi
exec /tmp/iw.orig "$@"
