/**
 * Electron main process.
 * Spawns the Python/FastAPI backend, waits for it to be ready,
 * then opens a BrowserWindow pointed at http://127.0.0.1:PORT.
 */

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')
const fs = require('fs')

const BACKEND_PORT = 8001
let pythonProcess = null
let mainWindow = null

// ── Python executable resolution ──────────────────────────────────────────────

function getPythonPath() {
  if (app.isPackaged) {
    // In a packaged build, look for a bundled Python alongside the backend.
    const candidates = [
      path.join(process.resourcesPath, 'python', 'python.exe'),
      path.join(process.resourcesPath, 'python', 'python'),
    ]
    for (const p of candidates) {
      if (fs.existsSync(p)) return p
    }
  }

  // Development: prefer the venv Python
  const venvCandidates = [
    path.join(__dirname, '..', 'backend', 'venv', 'Scripts', 'python.exe'),
    path.join(__dirname, '..', 'backend', 'venv', 'bin', 'python'),
    path.join(__dirname, '..', 'backend', '.venv', 'Scripts', 'python.exe'),
    path.join(__dirname, '..', 'backend', '.venv', 'bin', 'python'),
  ]
  for (const p of venvCandidates) {
    if (fs.existsSync(p)) return p
  }

  // Fallback: system Python
  return process.platform === 'win32' ? 'python' : 'python3'
}

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

// ── Start Python backend ───────────────────────────────────────────────────────

function startPythonBackend() {
  const pythonPath = getPythonPath()
  const backendDir = getBackendDir()
  const frontendDist = getFrontendDist()

  console.log('[Electron] Python    :', pythonPath)
  console.log('[Electron] BackendDir:', backendDir)
  console.log('[Electron] Frontend  :', frontendDist)

  pythonProcess = spawn(
    pythonPath,
    ['-m', 'uvicorn', 'app.main:app',
     '--host', '127.0.0.1',
     '--port', String(BACKEND_PORT),
     '--log-level', 'info'],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        PYTHONPATH: backendDir,
        APPTRACK_FRONTEND_DIST: frontendDist,
        // Prevent Python from buffering stdout so logs appear immediately
        PYTHONUNBUFFERED: '1',
      },
      windowsHide: true,
    }
  )

  pythonProcess.stdout.on('data', d => process.stdout.write('[Python] ' + d))
  pythonProcess.stderr.on('data', d => process.stderr.write('[Python] ' + d))

  pythonProcess.on('exit', (code, signal) => {
    console.log(`[Electron] Python exited: code=${code} signal=${signal}`)
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-exit', { code, signal })
    }
  })
}

// ── Wait for backend to become responsive ─────────────────────────────────────

function waitForBackend(maxRetries = 50, intervalMs = 400) {
  return new Promise((resolve, reject) => {
    let attempts = 0

    function probe() {
      attempts++
      const req = http.request(
        { hostname: '127.0.0.1', port: BACKEND_PORT, path: '/', method: 'GET', timeout: 1000 },
        () => { console.log('[Electron] Backend ready after', attempts, 'attempts'); resolve() }
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

// ── Create main window ────────────────────────────────────────────────────────

function createWindow() {
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
      // Allow video / microphone access for recording
      webSecurity: false,
    },
  })

  mainWindow.loadURL(`http://127.0.0.1:${BACKEND_PORT}`)

  mainWindow.once('ready-to-show', () => mainWindow.show())

  // Open external links in the OS browser, not in Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(`http://127.0.0.1:${BACKEND_PORT}`)) {
      shell.openExternal(url)
      return { action: 'deny' }
    }
    return { action: 'allow' }
  })

  mainWindow.on('closed', () => { mainWindow = null })
}

// ── IPC handlers ──────────────────────────────────────────────────────────────

ipcMain.handle('get-backend-port', () => BACKEND_PORT)

ipcMain.handle('get-app-version', () => app.getVersion())

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  startPythonBackend()

  try {
    await waitForBackend()
    createWindow()
  } catch (err) {
    console.error('[Electron]', err)
    dialog.showErrorBox(
      '启动失败',
      '后端服务启动超时。\n\n请确认 Python 环境已正确安装，并检查控制台输出。\n\n' + err.message
    )
    app.quit()
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (pythonProcess && !pythonProcess.killed) {
    console.log('[Electron] Terminating Python process...')
    pythonProcess.kill('SIGTERM')
    // On Windows SIGTERM may not work; send SIGKILL as fallback
    setTimeout(() => {
      if (!pythonProcess.killed) pythonProcess.kill('SIGKILL')
    }, 2000)
  }
})
