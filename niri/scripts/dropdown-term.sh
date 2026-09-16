#!/usr/bin/env bash
WINFILE="/tmp/kitty-dropdown.winid"
WIDTH=900
HEIGHT=650

focused_output() {
    niri msg -j workspaces 2>/dev/null | jq -r '.[] | select(.is_focused) | .output'
}

# Left edge that centers the window on the given output (coordinates are output-relative)
target_x() {
    niri msg -j outputs 2>/dev/null | \
        jq -r --arg out "$1" --argjson w "$WIDTH" '.[$out] | (.logical.width - $w) / 2 | round'
}

# Collapse to a 1x1 point at the window center, niri keeps floating windows on screen so it cannot be hidden
hide_window() {
    X=$(target_x "$(focused_output)")
    niri msg action set-window-width --id "$1" 1
    niri msg action set-window-height --id "$1" 1
    niri msg action move-floating-window --id "$1" -x "$((X + WIDTH / 2))" -y "$((HEIGHT / 2))"
    niri msg action focus-window-previous
}

# Grow from a point at the target center to full size at the top of the focused output
show_window() {
    OUTPUT=$(focused_output)
    WS=$(niri msg -j workspaces 2>/dev/null | jq -r '.[] | select(.is_focused) | .idx')
    niri msg action move-window-to-monitor --id "$1" "$OUTPUT"
    niri msg action move-window-to-workspace --window-id "$1" --focus false "$WS"
    niri msg action move-window-to-floating --id "$1"
    X=$(target_x "$OUTPUT")
    niri msg action set-window-width --id "$1" 1
    niri msg action set-window-height --id "$1" 1
    niri msg action move-floating-window --id "$1" -x "$((X + WIDTH / 2))" -y "$((HEIGHT / 2))"
    niri msg action set-window-width --id "$1" "$WIDTH"
    niri msg action set-window-height --id "$1" "$HEIGHT"
    niri msg action move-floating-window --id "$1" -x "$X" -y 0
    niri msg action focus-window --id "$1"
}

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
