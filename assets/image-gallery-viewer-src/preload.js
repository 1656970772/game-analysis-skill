const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("galleryBridge", {
  loadManifest: () => ipcRenderer.invoke("load-manifest"),
  openInFolder: (id) => ipcRenderer.invoke("open-in-folder", id),
  copyPath: (id) => ipcRenderer.invoke("copy-path", id)
});
