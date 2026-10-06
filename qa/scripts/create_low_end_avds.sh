#!/usr/bin/env bash
# One-time environment setup: creates the two low-end/low-resource AVDs the qa/ E2E suite
# expects (leak_test_low_1, leak_test_low_2). Not run automatically by pytest -- AVD creation is
# an environment-setup concern, not a per-test-run one. Re-run anytime to recreate them (e.g.
# after wiping ~/.android/avd); safe to run even if they already exist (each is skipped).
#
# Requires: $ANDROID_HOME set, and an x86_64 system image already installed under
# $ANDROID_HOME/system-images/ -- this reuses whatever API level/variant is already present
# rather than downloading a new one. If none is installed:
#   sdkmanager "system-images;android-<API>;google_apis;x86_64"
set -euo pipefail

: "${ANDROID_HOME:?Set ANDROID_HOME first}"
AVD_ROOT="$HOME/.android/avd"

SYSTEM_IMAGE_REL=$(find "$ANDROID_HOME/system-images" -mindepth 2 -maxdepth 2 -type d -iname "x86_64" 2>/dev/null | head -1)
if [ -z "$SYSTEM_IMAGE_REL" ]; then
  echo "No x86_64 system image found under \$ANDROID_HOME/system-images -- install one first, e.g.:" >&2
  echo "  sdkmanager \"system-images;android-36;google_apis;x86_64\"" >&2
  exit 1
fi
SYSTEM_IMAGE="${SYSTEM_IMAGE_REL#"$ANDROID_HOME"/}/"
API_LEVEL=$(echo "$SYSTEM_IMAGE_REL" | grep -oE 'android-[0-9.]+' | head -1 | cut -d- -f2)

create_avd() {
  local name=$1 ncore=$2 ram=$3 heap=$4 w=$5 h=$6 density=$7
  local avd_dir="$AVD_ROOT/${name}.avd"

  if [ -f "$AVD_ROOT/${name}.ini" ]; then
    echo "skip: $name already exists ($AVD_ROOT/${name}.ini)"
    return
  fi

  mkdir -p "$avd_dir"

  cat > "$AVD_ROOT/${name}.ini" <<EOF
avd.ini.encoding=UTF-8
path=$avd_dir
path.rel=avd/${name}.avd
target=android-$API_LEVEL
EOF

  cat > "$avd_dir/config.ini" <<EOF
AvdId=${name}
PlayStore.enabled = false
abi.type = x86_64
avd.ini.displayname = ${name}
avd.ini.encoding = UTF-8
disk.dataPartition.size = 2147483648
fastboot.forceColdBoot = yes
hw.accelerometer = no
hw.audioInput = no
hw.battery = yes
hw.camera.back = none
hw.camera.front = none
hw.cpu.arch = x86_64
hw.cpu.ncore = ${ncore}
hw.dPad = no
hw.device.manufacturer = Generic
hw.device.name = Nexus One
hw.gps = no
hw.gpu.enabled = yes
hw.gpu.mode = swiftshader_indirect
hw.gyroscope = no
hw.initialOrientation = portrait
hw.keyboard = yes
hw.lcd.density = ${density}
hw.lcd.height = ${h}
hw.lcd.width = ${w}
hw.mainKeys = no
hw.ramSize = ${ram}
hw.sdCard = yes
hw.sensors.light = no
hw.sensors.magnetic_field = no
hw.sensors.orientation = no
hw.sensors.pressure = no
hw.sensors.proximity = no
hw.trackBall = no
image.sysdir.1 = ${SYSTEM_IMAGE}
runtime.network.latency = full
runtime.network.speed = full
sdcard.size = 128M
showDeviceFrame = no
skin.dynamic = yes
skin.name = ${w}x${h}
tag.display = Google APIs
tag.id = google_apis_playstore
tag.ids = google_apis_playstore
vm.heapSize = ${heap}
EOF
  echo "created: $name (${ram}MB RAM, ${ncore} core(s), ${w}x${h}@${density}dpi, API $API_LEVEL)"
}

# Two distinct low-end profiles -- not duplicates of each other -- so a flow run across both
# isn't just testing one specific low-resource configuration twice.
create_avd "leak_test_low_1" 1 512 128 480 800 160
create_avd "leak_test_low_2" 2 768 192 720 1280 213
