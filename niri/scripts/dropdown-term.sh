#!/usr/bin/env bash
# Mod+T toggle for a floating kitty centered on the focused output.
# `dropdown-term.sh follow` (autostart) recenters it whenever niri moves it to another output.
WINFILE="/tmp/kitty-dropdown.winid"
WIDTH=900
HEIGHT=650

focused_output() {
    niri msg -j workspaces 2>/dev/null | jq -r '.[] | select(.is_focused) | .output'
}

# Top-left corner that centers the window on the given output (coordinates are output-relative)
target_pos() {
    niri msg -j outputs 2>/dev/null | \
        jq -r --arg out "$1" --argjson w "$WIDTH" --argjson h "$HEIGHT" '.[$out].logical | "\((.width - $w) / 2 | round) \((.height - $h) / 2 | round)"'
}

# Collapse to a 1x1 point at the window center, niri keeps floating windows on screen so it cannot be hidden
hide_window() {
    read -r X Y <<< "$(target_pos "$(focused_output)")"
    niri msg action set-window-width --id "$1" 1
    niri msg action set-window-height --id "$1" 1
    niri msg action move-floating-window --id "$1" -x "$((X + WIDTH / 2))" -y "$((Y + HEIGHT / 2))"
    # Focus the tiling layer on this output so the invisible point is no longer the workspace's active window
    niri msg action switch-focus-between-floating-and-tiling
    if [ "$(niri msg -j focused-window 2>/dev/null | jq -r .id)" = "$1" ]; then
        niri msg action focus-window-previous
    fi
}

# Grow from a point at the target center to full size at the center of the focused output
show_window() {
    OUTPUT=$(focused_output)
    WS=$(niri msg -j workspaces 2>/dev/null | jq -r '.[] | select(.is_focused) | .idx')
    niri msg action move-window-to-monitor --id "$1" "$OUTPUT"
    niri msg action move-window-to-workspace --window-id "$1" --focus false "$WS"
    niri msg action move-window-to-floating --id "$1"
    read -r X Y <<< "$(target_pos "$OUTPUT")"
    niri msg action set-window-width --id "$1" 1
    niri msg action set-window-height --id "$1" 1
    niri msg action move-floating-window --id "$1" -x "$((X + WIDTH / 2))" -y "$((Y + HEIGHT / 2))"
    niri msg action set-window-width --id "$1" "$WIDTH"
    niri msg action set-window-height --id "$1" "$HEIGHT"
    niri msg action move-floating-window --id "$1" -x "$X" -y "$Y"
    niri msg action focus-window --id "$1"
}

# Recenter on the output the window now sits on: collapse to a point and grow back, or just move the point if hidden
recenter_window() {
    INFO=$(niri msg -j windows 2>/dev/null | jq -r --argjson id "$1" '.[] | select(.id == $id) | "\(.workspace_id) \(.layout.tile_size[0])"')
    read -r WS_ID SIZE <<< "$INFO"
    OUTPUT=$(niri msg -j workspaces 2>/dev/null | jq -r --argjson ws "$WS_ID" '.[] | select(.id == $ws) | .output')
    read -r X Y <<< "$(target_pos "$OUTPUT")"
    niri msg action set-window-width --id "$1" 1
    niri msg action set-window-height --id "$1" 1
    niri msg action move-floating-window --id "$1" -x "$((X + WIDTH / 2))" -y "$((Y + HEIGHT / 2))"
    if [ "${SIZE%.*}" -ge 2 ]; then
        niri msg action set-window-width --id "$1" "$WIDTH"
        niri msg action set-window-height --id "$1" "$HEIGHT"
        niri msg action move-floating-window --id "$1" -x "$X" -y "$Y"
    fi
    # niri may still be placing the window after the monitor move, so settle it once more
    sleep 0.3
    if [ "${SIZE%.*}" -ge 2 ]; then
        niri msg action move-floating-window --id "$1" -x "$X" -y "$Y"
    else
        niri msg action move-floating-window --id "$1" -x "$((X + WIDTH / 2))" -y "$((Y + HEIGHT / 2))"
    fi
}

if [ "${1:-}" = "follow" ]; then
    LAST_WS=$(niri msg -j windows 2>/dev/null | jq -r --argjson id "$(cat "$WINFILE" 2>/dev/null || echo 0)" '.[] | select(.id == $id) | .workspace_id')
    niri msg -j event-stream | while read -r EVENT; do
        WIN_ID=$(cat "$WINFILE" 2>/dev/null) || continue
        WS_ID=$(jq -r --argjson id "$WIN_ID" '.WindowOpenedOrChanged.window | select(.id == $id) | .workspace_id // empty' <<< "$EVENT")
        [ -z "$WS_ID" ] && continue
        if [ -n "$LAST_WS" ] && [ "$WS_ID" != "$LAST_WS" ]; then
            sleep 0.1
            recenter_window "$WIN_ID"
        fi
        LAST_WS="$WS_ID"
    done
    exit
fi

# Cleanup stale tracking
if [ -f "$WINFILE" ]; then
    WIN_ID=$(cat "$WINFILE")
    EXISTS=$(niri msg -j windows 2>/dev/null | jq -r --argjson id "$WIN_ID" '.[] | select(.id == $id) | .id | tostring // empty')
    if [ -z "$EXISTS" ]; then
        rm -f "$WINFILE"
        WIN_ID=""
    fi
else
    WIN_ID=""
fi

if [ -z "$WIN_ID" ]; then
    kitty &
    GPID=$!

    WIN_ID=""
    for i in $(seq 1 30); do
        sleep 0.1
        WIN_ID=$(niri msg -j windows 2>/dev/null | jq -r --argjson pid "$GPID" '[.[] | select(.pid == $pid)] | .[0].id | tostring // empty')
        [ -n "$WIN_ID" ] && [ "$WIN_ID" != "null" ] && break
        WIN_ID=""
    done

    if [ -n "$WIN_ID" ]; then
        echo "$WIN_ID" > "$WINFILE"
        show_window "$WIN_ID"
    fi
else
    FOCUSED_ID=$(niri msg -j focused-window 2>/dev/null | jq -r '.id | tostring // empty')
    if [ "$WIN_ID" = "$FOCUSED_ID" ]; then
        hide_window "$WIN_ID"
    else
        show_window "$WIN_ID"
    fi
fi
