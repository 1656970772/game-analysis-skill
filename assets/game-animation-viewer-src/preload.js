const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("animationBridge", {
  loadManifest: () => ipcRenderer.invoke("animation:load-manifest"),
  preparePreview: (itemId, buildId, clipId) =>
    ipcRenderer.invoke("animation:prepare-preview", { itemId, buildId, clipId }),
  exportAnimation: (itemId, buildId, clipId, format, frame) =>
    ipcRenderer.invoke("animation:export", { itemId, buildId, clipId, format, frame }),
  openAsset: (resourceId) => ipcRenderer.invoke("animation:open-asset", resourceId),
  copyAssetPath: (resourceId) => ipcRenderer.invoke("animation:copy-asset-path", resourceId),
  openExport: (exportId) => ipcRenderer.invoke("animation:open-export", exportId)
});
