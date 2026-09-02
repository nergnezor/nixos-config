#!/usr/bin/env bash
# Keep the vertical monitor (DP-1) single-column:
# - the first window opened on it gets full width
# - any further window opened there is merged into that same column,
#   so it splits the height with what's already there instead of
#   opening as a second column beside it.
OUTPUT="DP-1"

declare -A ws_output   # workspace_id -> output name
declare -A win_ws      # window_id -> workspace_id (tiled windows only)
seeded=0

niri msg -j event-stream | while IFS= read -r line; do
    event=$(jq -r 'keys[0]' <<<"$line")

    case "$event" in
    WorkspacesChanged)
        ws_output=()
        while IFS=$'\t' read -r id output; do
            ws_output["$id"]="$output"
        done < <(jq -r '.WorkspacesChanged.workspaces[] | [.id, .output] | @tsv' <<<"$line")
        ;;
    WindowsChanged)
        win_ws=()
        while IFS=$'\t' read -r id ws; do
            win_ws["$id"]="$ws"
        done < <(jq -r '.WindowsChanged.windows[] | select(.is_floating|not) | [.id, .workspace_id] | @tsv' <<<"$line")
        seeded=1
        ;;
    WindowOpenedOrChanged)
        [ "$seeded" = 1 ] || continue
        id=$(jq -r '.WindowOpenedOrChanged.window.id' <<<"$line")
        ws=$(jq -r '.WindowOpenedOrChanged.window.workspace_id' <<<"$line")
        floating=$(jq -r '.WindowOpenedOrChanged.window.is_floating' <<<"$line")

        if [ "$floating" = "true" ]; then
            unset "win_ws[$id]"
            continue
        fi

        if [ -z "${win_ws[$id]:-}" ] && [ "${ws_output[$ws]:-}" = "$OUTPUT" ]; then
            siblings=0
            for w in "${!win_ws[@]}"; do
                [ "${win_ws[$w]}" = "$ws" ] && siblings=$((siblings + 1))
            done
            if [ "$siblings" -eq 0 ]; then
                niri msg action focus-window --id "$id" >/dev/null
                niri msg action set-column-width "100%" >/dev/null
            else
                niri msg action focus-window --id "$id" >/dev/null
                niri msg action focus-column-left >/dev/null
                niri msg action consume-window-into-column >/dev/null
            fi
        fi
        win_ws["$id"]="$ws"
        ;;
    WindowClosed)
        id=$(jq -r '.WindowClosed.id' <<<"$line")
        unset "win_ws[$id]"
        ;;
    esac
done
