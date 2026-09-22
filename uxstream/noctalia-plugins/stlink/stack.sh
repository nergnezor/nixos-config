#!/usr/bin/env sh
# Wait for a freshly launched helper window and stack it into its partner's column.
#   stack.sh <new-match> <partner-match>
# A match is a jq boolean over a niri window object, e.g. '.app_id == "usbcam"'.
set -eu
new_match="$1"
partner_match="$2"

find_window() {
    niri msg -j windows | jq -c "map(select($1)) | sort_by(.id) | last // empty"
}

new=""
for _ in $(seq 1 50); do
    new="$(find_window "$new_match")"
    [ -n "$new" ] && break
    sleep 0.1
done
[ -n "$new" ] || exit 0
partner="$(find_window "$partner_match and .id != $(echo "$new" | jq .id)")"
[ -n "$partner" ] || exit 0

new_id=$(echo "$new" | jq .id)
partner_id=$(echo "$partner" | jq .id)
prev_focus=$(niri msg -j windows | jq 'map(select(.is_focused)) | first.id // empty')

# The column actions below all act on the focused window.
niri msg action focus-window --id "$new_id"
new_ws=$(echo "$new" | jq .workspace_id)
partner_ws=$(echo "$partner" | jq .workspace_id)
if [ "$new_ws" != "$partner_ws" ]; then
    ws=$(niri msg -j workspaces | jq -c ".[] | select(.id == $partner_ws)")
    # Workspace indexes are per output, so land on the partner's output first.
    niri msg action move-column-to-monitor "$(echo "$ws" | jq -r .output)"
    niri msg action move-column-to-workspace "$(echo "$ws" | jq .idx)" --focus true
    new="$(find_window ".id == $new_id")"
    partner="$(find_window ".id == $partner_id")"
fi

new_col=$(echo "$new" | jq '.layout.pos_in_scrolling_layout[0]')
partner_col=$(echo "$partner" | jq '.layout.pos_in_scrolling_layout[0]')
# Removing the new column shifts the partner left when the new one sits before it.
if [ "$new_col" -lt "$partner_col" ]; then
    target=$partner_col
else
    target=$((partner_col + 1))
fi
niri msg action move-column-to-index "$target"
niri msg action consume-or-expel-window-left --id "$new_id"

[ -n "$prev_focus" ] && niri msg action focus-window --id "$prev_focus" || true
