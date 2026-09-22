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
-- Build selection lives here so it survives closing the panel while noctalia runs.
local buildType = "debug"
local buildEnv = "staging"
local feedbackError = false
local requestId = 0

local function tr(key, values)
  return noctalia.tr(key, values)
end

local function send(action, values)
  requestId += 1
  local command = { action = action, requestId = `panel-{requestId}` }
  for k, v in pairs(values or {}) do
    command[k] = v
  end
  noctalia.state.set(COMMAND_KEY, command)
end

-- Button label with its keyboard shortcut, matching the chords handled in onKey.
local function keyed(key, label)
  return `[{key}] {label}`
end

local function statusText()
  if snapshot.probe == "" then
    return tr("state.no_probe")
  elseif snapshot.debugger ~= "" then
    return tr("state.debugger", { name = snapshot.debugger })
  end
  return tr("state." .. tostring(snapshot.state))
end

-- Progress of the last build/flash job: bar while active, result line with a log button afterwards.
local function jobRow()
  local job = snapshot.job
  if type(job) ~= "table" then
    return ui.spacer({ height = 0 })
  end
  local percent = math.max(0, math.min(100, tonumber(job.percent) or 0))
  local fill = "primary"
  if job.phase == "error" then
    fill = "error"
  elseif job.phase == "done" then
    fill = "#73c936"
  end
  return ui.row({ gap = 8, align = "center" }, {
    ui.column({ gap = 3, flexGrow = 1 }, {
      ui.label({
        text = job.active and `{tostring(job.message)} · {percent}%` or tostring(job.message),
        fontSize = 11,
        color = job.phase == "error" and "error" or "on_surface_variant",
        maxLines = 1,
      }),
      ui.progress({ progress = percent / 100, fill = fill, track = "on_surface/0.10", radius = 3, height = 6 }),
    }),
    ui.button({ text = keyed("O", tr("actions.open_log")), glyph = "file-text", variant = "ghost", controlSize = "sm", onClick = "onOpenLog" }),
  })
end

local function render()
  local jobActive = type(snapshot.job) == "table" and snapshot.job.active == true
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
        text = keyed("S", halted and tr("actions.resume") or tr("actions.halt")),
        glyph = halted and "player-play" or "player-pause",
        variant = "primary",
        enabled = ready,
        onClick = "onToggle",
      }),
      ui.button({
        text = keyed("R", tr("actions.reset")),
        glyph = "reload",
        variant = "outline",
        enabled = ready,
        onClick = "onReset",
      }),
      ui.button({
        text = keyed("H", tr("actions.reset_halt")),
        glyph = "player-skip-back",
        variant = "outline",
        enabled = ready,
        onClick = "onResetHalt",
      }),
    }),
    ui.row({ gap = 6 }, {
      ui.button({
        text = keyed("K", tr("actions.camera")),
        glyph = snapshot.cameraRunning and "camera-off" or "camera",
        variant = snapshot.cameraRunning and "primary" or "outline",
        onClick = "onCamera",
      }),
      ui.button({
        text = keyed("L", tr("actions.serial")),
        glyph = snapshot.serialRunning and "terminal-2" or "terminal",
        variant = snapshot.serialRunning and "primary" or "outline",
        onClick = "onSerial",
      }),
    }),
    ui.row({ gap = 6, align = "center" }, {
      ui.label({ text = tr("panel.build"), fontSize = 11, color = "on_surface_variant" }),
      ui.button({
        text = keyed("D", buildType == "debug" and tr("build.debug") or tr("build.release")),
        glyph = buildType == "debug" and "bug" or "rocket",
        variant = "outline",
        onClick = "onToggleBuildType",
      }),
      ui.button({
        text = keyed("E", buildEnv == "staging" and tr("build.staging") or tr("build.production")),
        glyph = buildEnv == "staging" and "flask" or "building-factory-2",
        variant = "outline",
        onClick = "onToggleBuildEnv",
      }),
    }),
    ui.row({ gap = 6 }, {
      ui.button({ text = keyed("B", tr("actions.build")), glyph = "hammer", variant = "outline", enabled = not jobActive, onClick = "onBuild" }),
      ui.button({ text = keyed("F", tr("actions.flash")), glyph = "bolt", variant = "outline", enabled = ready and not jobActive, onClick = "onFlash" }),
      ui.button({ text = keyed("A", tr("actions.build_flash")), glyph = "player-track-next", variant = "primary", enabled = ready and not jobActive, onClick = "onBuildFlash" }),
    }),
    jobRow(),
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
function onToggleBuildType()
  buildType = buildType == "debug" and "release" or "debug"
  render()
end
function onToggleBuildEnv()
  buildEnv = buildEnv == "staging" and "production" or "staging"
  render()
end
function onBuild() send("build", { mode = "build", build = buildType, env = buildEnv }) end
function onFlash() send("build", { mode = "flash", build = buildType, env = buildEnv }) end
function onBuildFlash() send("build", { mode = "both", build = buildType, env = buildEnv }) end
function onOpenLog() send("open_log") end

local KEY_HANDLERS = {
  s = function() send("toggle") end,
  r = function() send("reset") end,
  h = function() send("reset_halt") end,
  k = onCamera,
  l = onSerial,
  d = onToggleBuildType,
  e = onToggleBuildEnv,
  b = onBuild,
  f = onFlash,
  a = onBuildFlash,
  o = onOpenLog,
}

function onKey(chord, pressed)
  if not pressed then
    return
  end
  local handler = KEY_HANDLERS[string.lower(tostring(chord))]
  if handler then
    handler()
  end
end
