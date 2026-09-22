--!nonstrict
-- ST-Link backend: polls MCU state and runs halt/resume/reset through one-shot openocd sessions.

local STATE_KEY = "stlink_snapshot"
local COMMAND_KEY = "stlink_command"
local RESULT_KEY = "stlink_result"

-- USB product ids of every ST-Link generation (vendor 0483).
local STLINK_PIDS = {
  ["3744"] = "V1", ["3748"] = "V2", ["374b"] = "V2-1", ["374a"] = "V2-1",
  ["374d"] = "V3", ["374e"] = "V3", ["374f"] = "V3", ["3752"] = "V2-1",
  ["3753"] = "V3", ["3754"] = "V3", ["3755"] = "V3", ["3757"] = "V3",
}

-- Anything holding the probe open. One-shot openocd would fail or steal it.
local DEBUGGERS = {
  openocd = true, ["ST-LINK_gdbserver"] = true, ["probe-rs"] = true,
  STM32_Programmer_CLI = true, ["st-util"] = true, pyocd = true,
}

local snapshot = {
  probe = "",        -- "" when no ST-Link is plugged in
  debugger = "",     -- process name holding the probe, "" when free
  state = "unknown", -- running | halted | reset | debug-running | unknown
  job = nil,         -- last build/flash job: phase, percent, message, active
  cameraRunning = false,
  serialRunning = false,
  busy = false,
  error = "",
  updatedAt = 0,
}

local pollPending = false
local actionBusy = false

local function trim(v)
  return noctalia.string.trim(tostring(v or ""))
end

local function shellQuote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

local function cfg(key, default)
  local v = trim(noctalia.getConfig(key))
  if v == "" then
    return default
  end
  return v
end

local function refreshIntervalMs()
  local s = tonumber(noctalia.getConfig("refresh_interval")) or 5
  return math.max(1, math.min(120, math.floor(s))) * 1000
end

local function nowSec()
  return math.floor((noctalia.nowMs() or 0) / 1000)
end

local function publish()
  snapshot.busy = actionBusy
  noctalia.state.set(STATE_KEY, snapshot)
end

local function result(command, ok, message)
  noctalia.state.set(RESULT_KEY, {
    requestId = command and command.requestId or "",
    action = command and command.action or "",
    ok = ok,
    message = message or "",
  })
end

-- openocd -d0 still logs to stderr, so target state is read from stdout via `puts`.
local function openocdCommand(tclCommands)
  local parts = {
    shellQuote(cfg("openocd_bin", "openocd")), "-d0",
    "-f", shellQuote(cfg("interface_cfg", "interface/stlink.cfg")),
    "-f", shellQuote(cfg("target_cfg", "target/stm32u5x.cfg")),
    "-c", "init",
  }
  for _, c in ipairs(tclCommands) do
    table.insert(parts, "-c")
    table.insert(parts, shellQuote(c))
  end
  table.insert(parts, "-c")
  table.insert(parts, shellQuote("puts [" .. cfg("target_name", "stm32u5x.cpu") .. " curstate]"))
  table.insert(parts, "-c")
  table.insert(parts, "shutdown")
  return table.concat(parts, " ")
end

local function parseState(stdout)
  for line in (tostring(stdout or "") .. "\n"):gmatch("(.-)\n") do
    line = trim(line)
    if line == "running" or line == "halted" or line == "reset" or line == "debug-running" or line == "unknown" then
      return line
    end
  end
  return ""
end

local function errorText(res, fallback)
  local err = trim(res and (res.stderr ~= "" and res.stderr or res.stdout) or "")
  local last = ""
  for line in (err .. "\n"):gmatch("(.-)\n") do
    line = trim(line)
    if line:match("^Error") then
      last = line
    end
  end
  if last ~= "" then
    return last
  end
  return err ~= "" and err or fallback
end

-- Helper apps are the plugin's own scripts. `match` identifies the running process for pgrep,
-- `window` is a jq filter over niri windows used by stack.sh.
local function cameraApp()
  return {
    command = "bash " .. shellQuote(noctalia.pluginDir() .. "/usbcam.sh") .. " " .. cfg("camera_args", "-r 270"),
    match = "wayland-app-id=usbcam",
    exact = false,
    window = '(.app_id == "usbcam")',
    startedKey = "result.camera_started",
    stoppedKey = "result.camera_stopped",
  }
end

local function serialApp()
  local baud = tostring(math.floor(tonumber(noctalia.getConfig("serial_baud")) or 2000000))
  return {
    command = "bash " .. shellQuote(noctalia.pluginDir() .. "/serial-log.sh") .. " " .. shellQuote(baud) .. " " .. shellQuote(cfg("serial_logdir", "/tmp/serial-logs")),
    match = "picocom",
    exact = true,
    window = '(.app_id == "kitty" and (.title | startswith("picocom ")))',
    startedKey = "result.serial_started",
    stoppedKey = "result.serial_stopped",
  }
end

-- Brackets the last char so the sh -c line carrying the pattern does not match itself.
local function selfSafe(pattern)
  return pattern:sub(1, -2) .. "[" .. pattern:sub(-1) .. "]"
end

-- Probe presence, a competing debugger and the helper apps are read without touching the probe.
local function probeCommand()
  return [[for d in /sys/bus/usb/devices/*; do [ "$(cat "$d/idVendor" 2>/dev/null)" = 0483 ] && cat "$d/idProduct"; done; echo ---; pgrep -x -l 'openocd|ST-LINK_gdbserver|probe-rs|STM32_Programmer_CLI|st-util|pyocd' | head -1; echo ---; pgrep -f -c ]] .. shellQuote(selfSafe(cameraApp().match)) .. [[ >/dev/null && echo camera; pgrep -x -c ]] .. shellQuote(serialApp().match) .. [[ >/dev/null && echo serial; true]]
end

local function scanProbe(callback)
  noctalia.runAsync(probeCommand(), function(res)
    local probe, debugger = "", ""
    local camera, serial = false, false
    local section = 0
    for line in (tostring(res and res.stdout or "") .. "\n"):gmatch("(.-)\n") do
      line = trim(line)
      if line == "---" then
        section += 1
      elseif section == 2 then
        camera = camera or line == "camera"
        serial = serial or line == "serial"
      elseif section == 1 then
        local name = line:match("^%d+%s+(%S+)")
        if name and DEBUGGERS[name] then
          debugger = name
        end
      elseif STLINK_PIDS[line:lower()] and probe == "" then
        probe = "ST-Link " .. STLINK_PIDS[line:lower()]
      end
    end
    callback(probe, debugger, camera, serial)
  end, 5000)
end

local function poll()
  if pollPending or actionBusy then
    return
  end
  pollPending = true
  scanProbe(function(probe, debugger, camera, serial)
    snapshot.probe = probe
    snapshot.debugger = debugger
    snapshot.cameraRunning = camera
    snapshot.serialRunning = serial
    if probe == "" or debugger ~= "" then
      snapshot.state = "unknown"
      snapshot.error = ""
      snapshot.updatedAt = nowSec()
      pollPending = false
      publish()
      return
    end
    noctalia.runAsync(openocdCommand({}), function(res)
      pollPending = false
      local state = res and res.exitCode == 0 and parseState(res.stdout) or ""
      if state ~= "" then
        snapshot.state = state
        snapshot.error = ""
      else
        snapshot.state = "unknown"
        snapshot.error = errorText(res, noctalia.tr("result.no_target"))
      end
      snapshot.updatedAt = nowSec()
      publish()
    end, 15000)
  end)
end

local function runAction(command, tclCommands, okKey)
  if actionBusy then
    result(command, false, noctalia.tr("result.busy"))
    return
  end
  if snapshot.probe == "" then
    result(command, false, noctalia.tr("result.no_probe"))
    return
  end
  if snapshot.debugger ~= "" then
    result(command, false, noctalia.tr("result.debugger", { name = snapshot.debugger }))
    return
  end
  actionBusy = true
  publish()
  noctalia.runAsync(openocdCommand(tclCommands), function(res)
    actionBusy = false
    local state = res and res.exitCode == 0 and parseState(res.stdout) or ""
    if state ~= "" then
      snapshot.state = state
      snapshot.error = ""
      snapshot.updatedAt = nowSec()
      local msg = noctalia.tr(okKey)
      result(command, true, msg)
      noctalia.notify(noctalia.tr("title"), msg)
    else
      snapshot.error = errorText(res, noctalia.tr("result.no_target"))
      local msg = noctalia.tr("result.failed", { error = snapshot.error })
      result(command, false, msg)
      noctalia.notifyError(noctalia.tr("title"), msg)
    end
    publish()
  end, 20000)
end

local function toggleApp(command, app, partner)
  local flag = app.exact and "-x " or "-f "
  local pattern = shellQuote(app.exact and app.match or selfSafe(app.match))
  -- The shell decides running/not running itself, so a stale snapshot cannot turn a start into a kill.
  local script = table.concat({
    "if pgrep " .. flag .. pattern .. " >/dev/null; then pkill " .. flag .. pattern .. " && echo stopped; exit; fi",
    -- Started as a transient unit, so a noctalia restart does not kill it.
    "if command -v systemd-run >/dev/null; then systemd-run --user --quiet --collect sh -c " .. shellQuote(app.command)
      .. "; else setsid -f sh -c " .. shellQuote(app.command) .. " </dev/null >/dev/null 2>&1; fi",
    -- Stacks the new window under its partner (stack.sh), detached so the toggle returns at once.
    "setsid -f sh " .. shellQuote(noctalia.pluginDir() .. "/stack.sh") .. " " .. shellQuote(app.window) .. " " .. shellQuote(partner.window) .. " >/dev/null 2>&1",
    "echo started",
  }, "\n")
  noctalia.runAsync(script, function(res)
    if res and res.exitCode == 0 then
      local stopped = trim(res.stdout) == "stopped"
      result(command, true, noctalia.tr(stopped and app.stoppedKey or app.startedKey))
    else
      local msg = noctalia.tr("result.failed", { error = errorText(res, "launch failed") })
      result(command, false, msg)
      noctalia.notifyError(noctalia.tr("title"), msg)
    end
    poll()
  end, 10000)
end

-- Build/flash runs headless (build-flash.sh) as a transient unit and reports through job.json,
-- which is polled at JOB_POLL_MS while a job is active.
local JOB_POLL_MS = 500
local jobActive = false

local function jobFile()
  return noctalia.pluginDataDir() .. "/job.json"
end

local function publishJob(job)
  snapshot.job = job
  publish()
end

local function pollJob()
  local raw = noctalia.readFile(jobFile())
  local job = raw and noctalia.json.decode(raw) or nil
  if type(job) ~= "table" then
    return
  end
  job.active = job.phase == "build" or job.phase == "flash"
  if not job.active and jobActive then
    jobActive = false
    noctalia.setUpdateInterval(refreshIntervalMs())
    if job.phase == "done" then
      noctalia.notify(noctalia.tr("title"), tostring(job.message))
    else
      noctalia.notifyError(noctalia.tr("title"), tostring(job.message))
    end
  end
  publishJob(job)
end

local function runBuild(command)
  if jobActive then
    result(command, false, noctalia.tr("result.busy"))
    return
  end
  local mode = tostring(command.mode or "build")
  local build = tostring(command.build or "debug")
  local env = tostring(command.env or "staging")
  local bank = tostring(math.floor(tonumber(noctalia.getConfig("bank")) or 0))
  local project = cfg("project_dir", "~/uxstream/embedded/client/projects/eyebuds"):gsub("^~", "$HOME")
  local inner = table.concat({
    "rm -f", shellQuote(jobFile()), "&&",
    "bash", shellQuote(noctalia.pluginDir() .. "/build-flash.sh"),
    shellQuote(mode), shellQuote(build), shellQuote(env), shellQuote(bank), '"' .. project .. '"',
    shellQuote(cfg("chip", "STM32U5G9BJ")), shellQuote(noctalia.pluginDataDir()),
  }, " ")
  local script = "if command -v systemd-run >/dev/null; then systemd-run --user --quiet --collect sh -c " .. shellQuote(inner)
    .. "; else setsid -f sh -c " .. shellQuote(inner) .. " </dev/null >/dev/null 2>&1; fi"
  jobActive = true
  publishJob({ phase = mode == "flash" and "flash" or "build", percent = 0, message = noctalia.tr("job.starting"), active = true })
  noctalia.setUpdateInterval(JOB_POLL_MS)
  noctalia.runAsync(script, function(res)
    if res and res.exitCode == 0 then
      result(command, true, noctalia.tr("result.build_started", { what = `{build} {env} bank{bank}` }))
    else
      jobActive = false
      noctalia.setUpdateInterval(refreshIntervalMs())
      local msg = noctalia.tr("result.failed", { error = errorText(res, "launch failed") })
      publishJob({ phase = "error", percent = 0, message = msg, active = false })
      result(command, false, msg)
      noctalia.notifyError(noctalia.tr("title"), msg)
    end
  end, 10000)
end

local function openJobLog()
  local log = noctalia.pluginDataDir() .. "/job.log"
  local inner = "kitty --title " .. shellQuote("eyebuds build log") .. " less +G " .. shellQuote(log)
  noctalia.runAsync("setsid -f sh -c " .. shellQuote(inner) .. " </dev/null >/dev/null 2>&1")
end

local function execute(command)
  if type(command) ~= "table" or type(command.action) ~= "string" then
    return
  end
  local action = command.action
  if action == "refresh" then
    poll()
  elseif action == "halt" then
    runAction(command, { "halt" }, "result.halted")
  elseif action == "resume" then
    runAction(command, { "resume" }, "result.resumed")
  elseif action == "toggle" then
    if snapshot.state == "halted" then
      runAction(command, { "resume" }, "result.resumed")
    else
      runAction(command, { "halt" }, "result.halted")
    end
  elseif action == "reset" then
    runAction(command, { "reset run" }, "result.reset")
  elseif action == "reset_halt" then
    runAction(command, { "reset halt" }, "result.reset_halted")
  elseif action == "toggle_camera" then
    toggleApp(command, cameraApp(), serialApp())
  elseif action == "toggle_serial" then
    toggleApp(command, serialApp(), cameraApp())
  elseif action == "build" then
    runBuild(command)
  elseif action == "open_log" then
    openJobLog()
  else
    result(command, false, "Unknown action: " .. action)
  end
end

noctalia.state.watch(COMMAND_KEY, execute)
noctalia.setUpdateInterval(refreshIntervalMs())
poll()

function update()
  if jobActive then
    pollJob()
  else
    poll()
  end
end

function onConfigChanged()
  noctalia.setUpdateInterval(refreshIntervalMs())
  poll()
end

-- `noctalia msg plugin erik/stlink:service all <halt|resume|toggle|reset|reset_halt|refresh|build> [json payload]`
function onIpc(event, payload)
  if type(payload) == "string" and payload ~= "" then
    payload = noctalia.json.decode(payload)
  end
  if type(payload) ~= "table" then
    payload = {}
  end
  payload.action = payload.action or tostring(event)
  payload.requestId = payload.requestId or ("ipc-" .. tostring(event))
  execute(payload)
end
