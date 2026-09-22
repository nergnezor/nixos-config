#!/usr/bin/env bash
# Build and/or flash one eyebuds bank in a terminal, then wait for a key so the output stays readable.
#   build-flash.sh <build|flash|both> <debug|release> <staging|production> <bank> <project-dir> <chip>
set -uo pipefail
mode="$1" build="$2" env="$3" bank="$4" project="$5" chip="$6"

case "$build" in
    debug)   preset="Debug";   artifact="bank$bank.elf"; format=(--binary-format elf) ;;
    release) preset="Release"; artifact="bank$bank.bin"
             # bankN.bin starts at its BIN_PREFIX origin (STM32U5g9xx_FLASH_BANK_*.ld).
             base=$([ "$bank" = 0 ] && echo 0x08020000 || echo 0x08210000)
             format=(--binary-format bin --base-address "$base") ;;
    *) echo "unknown build type: $build" >&2; exit 2 ;;
esac
case "$env" in
    staging)    preset+="Staging" ;;
    production) preset+="Production" ;;
    *) echo "unknown environment: $env" >&2; exit 2 ;;
esac

notify() {
    command -v notify-send >/dev/null && notify-send "$@" || true
}

finish() {
    if [ "$1" -eq 0 ]; then
        notify "ST-Link" "$2"
        echo; echo ">>> $2"; sleep 1.5
    else
        notify -u critical "ST-Link" "$2"
        echo; echo ">>> $2"; echo "Tryck Enter för att stänga."; read -r _
    fi
    exit "$1"
}

cd "$project" || finish 1 "Hittar inte $project"

# vcpkg-shell activate brings ninja and the ARM gcc but not cmake, which vcpkg also downloaded.
if ! command -v cmake >/dev/null; then
    for d in "$HOME"/.vcpkg/downloads/artifacts/*/tools.kitware.cmake/*/bin; do
        [ -x "$d/cmake" ] && PATH="$d:$PATH" && break
    done
    export PATH
fi

if [ "$mode" != flash ]; then
    echo ">>> make ${build}_${env}_bank${bank}"
    make "${build}_${env}_bank${bank}" || finish $? "Bygget misslyckades: $preset bank$bank"
fi

if [ "$mode" != build ]; then
    file="build/$preset/$artifact"
    [ -f "$file" ] || finish 1 "Saknas: $file"
    echo ">>> probe-rs download $file"
    # The bar widget polls the probe with openocd, so the first attempt can find it busy.
    for attempt in 1 2 3 4 5; do
        probe-rs download --chip "$chip" --protocol swd --speed 4000 --non-interactive "${format[@]}" "$file" && break
        [ "$attempt" -eq 5 ] && finish 1 "Flashning misslyckades: $file"
        sleep 1
    done
    probe-rs reset --chip "$chip" --protocol swd || finish $? "Reset efter flashning misslyckades"
fi

case "$mode" in
    build) finish 0 "Byggt: $preset bank$bank" ;;
    flash) finish 0 "Flashat: $preset bank$bank" ;;
    *)     finish 0 "Byggt och flashat: $preset bank$bank" ;;
esac
