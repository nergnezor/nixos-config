#!/usr/bin/env bash
WINFILE="/tmp/ghostty-dropdown.winid"

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
    ghostty --gtk-single-instance=false &
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
        niri msg action focus-window --id "$WIN_ID"
        niri msg action move-window-to-floating --id "$WIN_ID"
        niri msg action set-window-width --id "$WIN_ID" 600
        niri msg action set-window-height --id "$WIN_ID" 650
        # Center horizontally on the focused output and snap to its top edge
        # Coordinates are output-relative (not global), so no offset for output position
        OUTPUT=$(niri msg -j workspaces 2>/dev/null | jq -r '.[] | select(.is_focused) | .output')
        CENTER_X=$(niri msg -j outputs 2>/dev/null | \
            jq -r --arg out "$OUTPUT" --argjson w 600 \
            '.[$out] | (.logical.width - $w) / 2 | round')
        niri msg action move-floating-window --id "$WIN_ID" -x "$CENTER_X" -y 0
    fi
else
    FOCUSED_ID=$(niri msg -j focused-window 2>/dev/null | jq -r '.id | tostring // empty')
    if [ "$WIN_ID" = "$FOCUSED_ID" ]; then
        niri msg action close-window
        rm -f "$WINFILE"
    else
        niri msg action focus-window --id "$WIN_ID"
    fi
fi
