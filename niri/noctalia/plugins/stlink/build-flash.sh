#!/usr/bin/env bash
# Headless build and/or flash of one eyebuds bank. Progress goes to <state-dir>/job.json for the
# panel's progress bar, the full output to <state-dir>/job.log.
#   build-flash.sh <build|flash|both> <debug|release> <staging|production> <bank> <project-dir> <chip> <state-dir>
set -uo pipefail
mode="$1" build="$2" env="$3" bank="$4" project="$5" chip="$6" state="$7"
mkdir -p "$state"
log="$state/job.log"
: > "$log"

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

# Written atomically so a half-written file is never read by the service.
progress() { # <phase> <percent> <message>
    jq -cn --arg phase "$1" --argjson percent "$2" --arg message "$3" --arg pid "$$" \
        '{phase: $phase, percent: $percent, message: $message, pid: ($pid | tonumber)}' > "$state/job.json.tmp"
    mv "$state/job.json.tmp" "$state/job.json"
}
fail() { progress error 0 "$1"; exit 1; }

cd "$project" || fail "Hittar inte $project"

# vcpkg-shell activate brings ninja and the ARM gcc but not cmake, which vcpkg also downloaded.
if ! command -v cmake >/dev/null; then
    for d in "$HOME"/.vcpkg/downloads/artifacts/*/tools.kitware.cmake/*/bin; do
        [ -x "$d/cmake" ] && PATH="$d:$PATH" && break
    done
    export PATH
fi

if [ "$mode" != flash ]; then
    progress build 0 "Konfigurerar $preset"
    # Ninja prints "[done/total] step", the only percent source the build has.
    make "${build}_${env}_bank${bank}" 2>&1 | while IFS= read -r line; do
        printf '%s\n' "$line" >> "$log"
        if [[ $line =~ ^\[([0-9]+)/([0-9]+)\] ]]; then
            progress build $(( 100 * BASH_REMATCH[1] / BASH_REMATCH[2] )) "Bygger $preset ${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
        fi
    done
    [ "${PIPESTATUS[0]}" -eq 0 ] || fail "Bygget misslyckades: $(grep -m1 -E 'error|FAILED' "$log" | cut -c1-120)"
fi

if [ "$mode" != build ]; then
    file="build/$preset/$artifact"
    [ -f "$file" ] || fail "Saknas: $file"
    progress flash 0 "Flashar $artifact"
    # The bar widget polls the probe with openocd, so the first attempt can find it busy.
    for attempt in 1 2 3 4 5; do
        # script(1) gives probe-rs a pty so its progress bar (with percentages) is printed.
        script -qfec "probe-rs download --chip '$chip' --protocol swd --speed 4000 --non-interactive ${format[*]} '$file'" /dev/null 2>&1 \
        | tr '\r' '\n' | while IFS= read -r line; do
            printf '%s\n' "$line" >> "$log"
            if [[ $line =~ ([0-9]+)% ]]; then
                progress flash "${BASH_REMATCH[1]}" "Flashar $artifact"
            fi
        done
        [ "${PIPESTATUS[0]}" -eq 0 ] && break
        [ "$attempt" -eq 5 ] && fail "Flashning misslyckades: $(grep -m1 -i 'error' "$log" | cut -c1-120)"
        sleep 1
    done
    progress flash 100 "Reset"
    probe-rs reset --chip "$chip" --protocol swd >> "$log" 2>&1 || fail "Reset efter flashning misslyckades"
fi

case "$mode" in
    build) progress done 100 "Byggt: $preset bank$bank" ;;
    flash) progress done 100 "Flashat: $preset bank$bank" ;;
    *)     progress done 100 "Byggt och flashat: $preset bank$bank" ;;
esac
