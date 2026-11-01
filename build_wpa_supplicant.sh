#!/bin/sh
# Cross-build a minimal static wpa_supplicant for the Wolfbox G930
# (ARMv7 Cortex-A7, 8733bu nl80211 driver, ~2 MB free RAM).
#
#   sh build_wpa_supplicant.sh ZIG_DIR WPA_SRC_DIR LIBNL_TINY_DIR OUT_DIR
#
# Only WPA2-PSK client support: no EAP/WPS/P2P/AP/ctrl_iface, internal
# crypto (no OpenSSL), libnl-tiny instead of libnl. Static musl so it
# doesn't depend on the camera's libraries.
set -eu
ZIG="$1/zig"; WPA="$2"; NL="$3"; OUT="$4"
TARGET="-target arm-linux-musleabihf -mcpu=cortex_a7"
OPT="-Os -ffunction-sections -fdata-sections"
mkdir -p "$OUT/nl"

# libnl-tiny -> libnl-tiny.a
for f in attr cache cache_mngt error genl genl_ctrl genl_family genl_mngt handlers msg nl object socket unl; do
    "$ZIG" cc $TARGET $OPT -I"$NL/include" -c "$NL/$f.c" -o "$OUT/nl/$f.o"
done
rm -f "$OUT/nl/libnl-tiny.a"
"$ZIG" ar rcs "$OUT/nl/libnl-tiny.a" "$OUT"/nl/*.o

cat > "$WPA/wpa_supplicant/.config" <<'EOF'
CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL_TINY=y
CONFIG_TLS=internal
CONFIG_CRYPTO=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_BACKEND=file
CONFIG_NO_CONFIG_WRITE=y
CONFIG_NO_CONFIG_BLOBS=y
CONFIG_NO_RANDOM_POOL=y
CONFIG_ELOOP=eloop
CONFIG_OS=unix
CONFIG_L2_PACKET=linux
CONFIG_SME=y
CONFIG_CTRL_IFACE=y
EOF

make -C "$WPA/wpa_supplicant" clean >/dev/null
make -C "$WPA/wpa_supplicant" -j8 wpa_supplicant wpa_cli \
    CC="$ZIG cc $TARGET" \
    PKG_CONFIG=true \
    EXTRA_CFLAGS="$OPT -D_GNU_SOURCE -I$NL/include" \
    LDFLAGS="-static -L$OUT/nl -Wl,--gc-sections -s"

cp "$WPA/wpa_supplicant/wpa_supplicant" "$WPA/wpa_supplicant/wpa_cli" "$OUT/"
ls -la "$OUT/wpa_supplicant" "$OUT/wpa_cli"
