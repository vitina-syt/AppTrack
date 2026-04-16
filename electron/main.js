/**
 * Electron main process.
 *
 * 两种模式，通过 electron/config.json 的 backendUrl 字段控制：
 *
 *   远程模式：backendUrl = "http://139.24.234.55"
 *     → Electron 窗口打开远程服务器（Gallery / Editor）。
 *       同时在本机启动本地 Python，用于录制 API（127.0.0.1:8001）。
 *       isRemoteMode 在前端检测 hostname，录制 API 走 localApi，
 *       Gallery / Sync API 走相对路径（指向远程服务器）。
 *
 *   本地模式：backendUrl = ""  （留空）
 *     → 在本机启动 Python 后端，窗口打开 127.0.0.1:8001。
 *       需要本机有 Python 环境或 PyInstaller 打包的 exe。
 *
 * 无论哪种模式，本地 Python 都会启动，以保证录制 API 可用。
 */

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const https = require('https')
const fs = require('fs')

const LOCAL_PORT = 8001
let pythonProcess = null
let mainWindow = null

// ── 读取配置 ──────────────────────────────────────────────────────────────────

function loadConfig() {
  const configPath = app.isPackaged
    ? path.join(process.resourcesPath, 'electron', 'config.json')
    : path.join(__dirname, 'config.json')
  try {
    return JSON.parse(fs.readFileSync(configPath, 'utf8'))
  } catch {
    return { backendUrl: '' }
  }
}

const config = loadConfig()

// 规范化 backendUrl：去掉末尾斜杠，空字符串表示本地模式
const REMOTE_URL = (config.backendUrl || '').trim().replace(/\/+$/, '')
const IS_REMOTE  = REMOTE_URL.length > 0

// 本地模式下的后端地址
const LOCAL_URL  = `http://127.0.0.1:${LOCAL_PORT}`

// 最终打开的地址
const BACKEND_URL = IS_REMOTE ? REMOTE_URL : LOCAL_URL

console.log(`[Electron] Mode: ${IS_REMOTE ? 'REMOTE → ' + REMOTE_URL : 'LOCAL → ' + LOCAL_URL}`)

// ── 本地模式：启动 Python ─────────────────────────────────────────────────────

function getBackendDir() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : path.join(__dirname, '..', 'backend')
}

function getFrontendDist() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'frontend', 'dist')
    : path.join(__dirname, '..', 'frontend', 'dist')
}

function resolveBackendLaunch() {
  const frontendDist = getFrontendDist()
  const backendDir   = getBackendDir()

  // Option 1: PyInstaller 打包的 exe
  const pyiExe = path.join(backendDir, 'dist', 'apptrack_backend', 'apptrack_backend.exe')
  if (fs.existsSync(pyiExe)) {
    return {
      cmd:  pyiExe,
      args: ['--port', String(LOCAL_PORT), '--frontend-dist', frontendDist],
      cwd:  backendDir,
      env:  { ...process.env, PYTHONUNBUFFERED: '1' },
    }
  }

  // Option 2: venv Python
  const venvCandidates = [
    path.join(backendDir, 'venv',  'Scripts', 'python.exe'),
    path.join(backendDir, 'venv',  'bin',     'python'),
    path.join(backendDir, '.venv', 'Scripts', 'python.exe'),
    path.join(backendDir, '.venv', 'bin',     'python'),
  ]
  const pythonPath = venvCandidates.find(p => fs.existsSync(p))
    ?? (process.platform === 'win32' ? 'python' : 'python3')

  return {
    cmd:  pythonPath,
    args: ['-m', 'uvicorn', 'app.main:app',
           '--host', '127.0.0.1',
           '--port', String(LOCAL_PORT),
           '--log-level', 'info'],
    cwd:  backendDir,
    env:  {
      ...process.env,
      PYTHONPATH:             backendDir,
      APPTRACK_FRONTEND_DIST: frontendDist,
      PYTHONUNBUFFERED:       '1',
    },
  }
}

function startPythonBackend() {
  const { cmd, args, cwd, env } = resolveBackendLaunch()
  console.log('[Electron] Starting local backend:', cmd, args.join(' '))
  pythonProcess = spawn(cmd, args, { cwd, env, windowsHide: true })
  pythonProcess.stdout.on('data', d => process.stdout.write('[Python] ' + d))
  pythonProcess.stderr.on('data', d => process.stderr.write('[Python] ' + d))
  pythonProcess.on('exit', (code, signal) => {
    console.log(`[Electron] Backend exited: code=${code} signal=${signal}`)
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-exit', { code, signal })
    }
  })
}

// ── 等待后端就绪 ──────────────────────────────────────────────────────────────

function waitForBackend(url, maxRetries = 50, intervalMs = 400) {
  return new Promise((resolve, reject) => {
    const parsed   = new URL(url)
    const useHttps = parsed.protocol === 'https:'
    const lib      = useHttps ? https : http
    const port     = parsed.port
      ? parseInt(parsed.port)
      : (useHttps ? 443 : 80)

    let attempts = 0

    function probe() {
      attempts++
      const req = lib.request(
        { hostname: parsed.hostname, port, path: '/', method: 'GET', timeout: 2000,
          rejectUnauthorized: false },
        () => { console.log(`[Electron] Backend ready after ${attempts} attempt(s)`); resolve() }
      )
      req.on('error', () => {
        if (attempts >= maxRetries) {
          reject(new Error(`Backend did not start after ${attempts} attempts`))
        } else {
          setTimeout(probe, intervalMs)
        }
      })
      req.on('timeout', () => { req.destroy(); setTimeout(probe, intervalMs) })
      req.end()
    }

    probe()
  })
}

// ── 创建窗口 ──────────────────────────────────────────────────────────────────

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 600,
    title: 'AppTrack',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: false,
    },
  })

  mainWindow.loadURL(url)
  mainWindow.once('ready-to-show', () => mainWindow.show())

  mainWindow.webContents.setWindowOpenHandler(({ url: openUrl }) => {
    if (!openUrl.startsWith(BACKEND_URL)) {
      shell.openExternal(openUrl)
      return { action: 'deny' }
    }
    return { action: 'allow' }
  })

  mainWindow.on('closed', () => { mainWindow = null })
}

// ── IPC ───────────────────────────────────────────────────────────────────────

ipcMain.handle('get-backend-port', () => LOCAL_PORT)
ipcMain.handle('get-app-version',  () => app.getVersion())
ipcMain.handle('get-backend-url',  () => BACKEND_URL)
ipcMain.handle('is-remote',        () => IS_REMOTE)

// ── App 生命周期 ──────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  // 始终启动本地 Python，供录制 API（127.0.0.1:8001）使用
  startPythonBackend()

  // 等待本地后端就绪（录制功能依赖本地 Python）
  try {
    await waitForBackend(LOCAL_URL)
  } catch (err) {
    console.error('[Electron] Local backend failed:', err)
    dialog.showErrorBox(
      '启动失败',
      `本地录制服务启动超时。\n\n请确认 Python 环境已正确安装，并检查控制台输出。\n\n${err.message}`
    )
    app.quit()
    return
  }

  // 远程模式：额外检查远程服务器是否可达
  if (IS_REMOTE) {
    try {
      await waitForBackend(REMOTE_URL, 30, 500)
    } catch (err) {
      console.error('[Electron] Remote server unreachable:', err)
      dialog.showErrorBox(
        '启动失败',
        `无法连接到服务器：${REMOTE_URL}\n\n请检查服务器是否在线，以及网络是否可以访问。\n\n${err.message}`
      )
      app.quit()
      return
    }
  }

  createWindow(BACKEND_URL)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(BACKEND_URL)
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (pythonProcess && !pythonProcess.killed) {
    console.log('[Electron] Terminating Python process...')
    pythonProcess.kill('SIGTERM')
    setTimeout(() => {
      if (!pythonProcess.killed) pythonProcess.kill('SIGKILL')
    }, 2000)
  }
})
