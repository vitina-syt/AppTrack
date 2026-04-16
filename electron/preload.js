/**
 * Electron preload script.
 * Runs in the renderer's context with Node access,
 * then exposes a safe, narrow API to the web page via contextBridge.
 */

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  /** True when running inside Electron (vs. plain browser). */
  isElectron: true,

  /** Ask the main process for the backend port. */
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),

  /** App version string (package.json). */
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),

  /** Subscribe to backend-exit events (e.g. show an error banner). */
  onBackendExit: (callback) => {
    ipcRenderer.on('backend-exit', (_event, payload) => callback(payload))
  },
})
