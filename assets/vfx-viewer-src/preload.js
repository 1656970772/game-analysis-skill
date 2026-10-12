const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('gameVfxViewer', {
  getInitialData: () => ipcRenderer.invoke('vfx:get-initial-data'),
  readReconstruction: (relativePath) => ipcRenderer.invoke('vfx:read-reconstruction', relativePath),
  readOriginalPrefabPreview: (relativePath) => ipcRenderer.invoke('vfx:read-original-prefab-preview', relativePath),
  openUnityExport: () => ipcRenderer.invoke('vfx:open-unity-export'),
});
