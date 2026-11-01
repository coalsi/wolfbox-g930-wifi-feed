#!/bin/sh
# wifi_sta: bind-mounted over /customer/wifi/net_toggle.sh by supervisor.sh
# (RAM only; gone after a reboot until the supervisor puts it back).
#
# The firmware turns Wi-Fi OFF about 2 minutes after it's switched on if no
# device has joined the camera's own AP, which is always the case when the
# camera is the one joining a network. While saved networks exist, that OFF
# is dropped; put a file named WIFI_OFF on the SD card to really turn Wi-Fi
# off. Everything else goes to the stock script.
if [ "$1" = "OFF" ] && grep -q '^network=' /oneed_cust/wifi_sta/networks.conf 2>/dev/null && \
   [ ! -f /mnt/mmc/WIFI_OFF ]; then
    echo "$(date +%T) net_toggle OFF ignored (saved networks)" >> /mnt/mmc/wifi_sta.log
    exit 0
fi
exec sh /tmp/net_toggle.orig.sh "$@"
