const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("modelViewerBridge", {
  getRuntime: () => ipcRenderer.invoke("get-runtime"),
  readManifest: () => ipcRenderer.invoke("read-manifest"),
  openFolder: (payload) => ipcRenderer.invoke("open-folder", payload),
  copyPath: (payload) => ipcRenderer.invoke("copy-path", payload)
});
