#!/usr/bin/env bash
set -euo pipefail

# Mouseless was installed via flatpak while Cursor/VS Code (snap) owned
# XDG_DATA_HOME, so the app may live under ~/snap/code/*/… rather than
# ~/.local/share/flatpak. Autostart/systemd has no snap env — find it.
flatpak_data_home=""
for candidate in \
    "${XDG_DATA_HOME:-}" \
    "$HOME/.local/share" \
    "$HOME/snap/code"/*/.local/share; do
    [[ -n "$candidate" && -d "$candidate/flatpak/app/net.sonuscape.mouseless" ]] || continue
    flatpak_data_home="$candidate"
    break
done

if [[ -z "$flatpak_data_home" ]]; then
    echo "mouseless flatpak install not found under ~/.local/share or ~/snap/code" >&2
    exit 1
fi

export XDG_DATA_HOME="$flatpak_data_home"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

# Wayland compositor + flatpak session helper need a moment after login.
sleep 5

exec /usr/bin/flatpak run \
    --branch=stable --arch=x86_64 --command=mouseless-wrapper \
    net.sonuscape.mouseless
