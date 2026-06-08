const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("galleryBridge", {
  setContextItem: (payload) => ipcRenderer.invoke("set-context-item", payload),
  openInFolder: (payload) => ipcRenderer.invoke("open-in-folder", payload),
  copyPath: (payload) => ipcRenderer.invoke("copy-path", payload)
});
