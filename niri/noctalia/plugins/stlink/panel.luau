--!nonstrict
-- Control panel: halt / resume / reset buttons plus last action feedback.

local STATE_KEY = "stlink_snapshot"
local COMMAND_KEY = "stlink_command"
local RESULT_KEY = "stlink_result"

local snapshot = noctalia.state.get(STATE_KEY) or {
  probe = "",
  debugger = "",
  state = "unknown",
  cameraRunning = false,
  serialRunning = false,
  busy = false,
  error = "",
  updatedAt = 0,
}
local feedback = ""
local feedbackError = false
local requestId = 0

local function tr(key, values)
  return noctalia.tr(key, values)
end

local function send(action)
  requestId += 1
  noctalia.state.set(COMMAND_KEY, { action = action, requestId = `panel-{requestId}` })
end

local function statusText()
  if snapshot.probe == "" then
    return tr("state.no_probe")
  elseif snapshot.debugger ~= "" then
    return tr("state.debugger", { name = snapshot.debugger })
  end
  return tr("state." .. tostring(snapshot.state))
end

local function render()
  local ready = snapshot.probe ~= "" and snapshot.debugger == "" and not snapshot.busy
  local halted = snapshot.state == "halted"
  local color = "on_surface_variant"
  if ready and snapshot.state == "running" then
    color = "#73c936"
  elseif ready and halted then
    color = "#e5a50a"
  end

  local notes = {}
  if snapshot.error ~= "" then
    table.insert(notes, ui.label({ text = snapshot.error, color = "error", fontSize = 11, maxLines = 2 }))
  end
  if feedback ~= "" then
    table.insert(notes, ui.label({
      text = feedback,
      color = feedbackError and "error" or "on_surface_variant",
      fontSize = 11,
      maxLines = 2,
    }))
  end

  panel.render(ui.column({ flexGrow = 1, gap = 10 }, {
    ui.row({ align = "center", gap = 10 }, {
      ui.glyph({ name = "cpu", size = 28, color = color }),
      ui.column({ flexGrow = 1, gap = 0 }, {
        ui.label({ text = tr("title"), fontSize = 18, fontWeight = "bold" }),
        ui.label({
          text = (snapshot.probe ~= "" and (snapshot.probe .. " · ") or "") .. statusText(),
          fontSize = 11,
          color = "on_surface_variant",
        }),
      }),
      ui.button({ glyph = "refresh", variant = "ghost", onClick = "onRefresh" }),
      ui.button({ glyph = "close", onClick = "onClose" }),
    }),
    ui.row({ gap = 6 }, {
      ui.button({
        text = halted and tr("actions.resume") or tr("actions.halt"),
        glyph = halted and "player-play" or "player-pause",
        variant = "primary",
        enabled = ready,
        onClick = "onToggle",
      }),
      ui.button({
        text = tr("actions.reset"),
        glyph = "reload",
        variant = "outline",
        enabled = ready,
        onClick = "onReset",
      }),
      ui.button({
        text = tr("actions.reset_halt"),
        glyph = "player-skip-back",
        variant = "outline",
        enabled = ready,
        onClick = "onResetHalt",
      }),
    }),
    ui.row({ gap = 6 }, {
      ui.button({
        text = tr("actions.camera"),
        glyph = snapshot.cameraRunning and "camera-off" or "camera",
        variant = snapshot.cameraRunning and "primary" or "outline",
        onClick = "onCamera",
      }),
      ui.button({
        text = tr("actions.serial"),
        glyph = snapshot.serialRunning and "terminal-2" or "terminal",
        variant = snapshot.serialRunning and "primary" or "outline",
        onClick = "onSerial",
      }),
    }),
    ui.column({ gap = 3, align = "stretch" }, notes),
    ui.spacer({ flexGrow = 1 }),
    ui.label({
      text = (snapshot.updatedAt or 0) > 0
        and tr("panel.updated", { time = noctalia.formatTime("%H:%M:%S", snapshot.updatedAt) })
        or "",
      color = "on_surface_variant",
      fontSize = 11,
    }),
  }))
end

noctalia.state.watch(STATE_KEY, function(value)
  if type(value) == "table" then
    snapshot = value
    render()
  end
end)

noctalia.state.watch(RESULT_KEY, function(result)
  if type(result) ~= "table" then
    return
  end
  if type(result.requestId) ~= "string" or not result.requestId:match("^panel%-") then
    return
  end
  feedback = tostring(result.message or "")
  feedbackError = result.ok ~= true
  render()
end)

function onOpen(_context)
  feedback = ""
  send("refresh")
  render()
end

function onClose() panel.close() end
function onRefresh() send("refresh") end
function onToggle() send("toggle") end
function onReset() send("reset") end
function onResetHalt() send("reset_halt") end
function onCamera() send("toggle_camera") end
function onSerial() send("toggle_serial") end
