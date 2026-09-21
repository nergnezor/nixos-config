--!nonstrict
-- Bar widget: MCU state as a tinted cpu glyph. Click toggles halt/resume, right click opens the panel.

local PANEL_ID = "erik/stlink:control"
local STATE_KEY = "stlink_snapshot"
local COMMAND_KEY = "stlink_command"

local COLORS = {
  running = "#73c936",
  halted = "#e5a50a",
  reset = "#e5a50a",
  ["debug-running"] = "#73c936",
}

local snapshot = noctalia.state.get(STATE_KEY) or {
  probe = "",
  debugger = "",
  state = "unknown",
  busy = false,
  error = "",
}

local requestId = 0

local function send(action)
  requestId += 1
  noctalia.state.set(COMMAND_KEY, { action = action, requestId = `widget-{requestId}` })
end

local function statusText()
  if snapshot.probe == "" then
    return noctalia.tr("state.no_probe")
  elseif snapshot.debugger ~= "" then
    return noctalia.tr("state.debugger", { name = snapshot.debugger })
  elseif snapshot.busy then
    return noctalia.tr("state.busy")
  end
  return noctalia.tr("state." .. tostring(snapshot.state))
end

local function render()
  local color = "on_surface_variant"
  if snapshot.probe ~= "" and snapshot.debugger == "" then
    color = COLORS[snapshot.state] or "error"
  elseif snapshot.debugger ~= "" then
    color = "primary"
  end

  local children = {
    ui.glyph({ name = "cpu", size = 16, color = color }),
  }
  if noctalia.getConfig("show_label") ~= false and snapshot.probe ~= "" and snapshot.debugger == "" then
    table.insert(children, ui.label({
      text = statusText(),
      fontWeight = "bold",
      color = "on_surface",
    }))
  end

  local container = barWidget.isVertical() and ui.column or ui.row
  barWidget.render(container({ gap = 5, align = "center" }, children))

  local tooltip = statusText()
  if snapshot.probe ~= "" then
    tooltip = snapshot.probe .. " · " .. tooltip
  end
  if snapshot.error ~= "" then
    tooltip = tooltip .. "\n" .. snapshot.error
  end
  barWidget.setTooltip(tooltip)
end

noctalia.state.watch(STATE_KEY, function(value)
  if type(value) == "table" then
    snapshot = value
    render()
  end
end)

render()

function onClick()
  send("toggle")
end

function onRightClick()
  noctalia.togglePanel(PANEL_ID)
end

function onMiddleClick()
  send("reset")
end
