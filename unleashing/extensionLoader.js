const fs = window.nodeRequire('fs');
const path = window.nodeRequire('path');

class ExtensionLoader {
  constructor(apiInstance) {
    this.api = apiInstance;
    this.loadedExtensions = new Map();
  }

  async loadExtensions(extensionsPath) {
    this.loadedExtensions.clear();

    const listEl = document.getElementById('extensions-list');
    if (listEl) {
      listEl.innerHTML = '';
    }

    if (!fs.existsSync(extensionsPath)) {
      try {
        fs.mkdirSync(extensionsPath, { recursive: true });
      } catch (e) {
        console.error("Failed to create extensions directory:", e);
        if (listEl) listEl.innerHTML = '<p style="padding: 10px; color: var(--text-secondary); text-align: center;">Failed to access extensions folder.</p>';
        return;
      }
    }

    let folders = [];
    try {
      folders = fs.readdirSync(extensionsPath);
    } catch (e) {
      console.error("Failed to read extensions directory:", e);
      if (listEl) listEl.innerHTML = '<p style="padding: 10px; color: var(--text-secondary); text-align: center;">Failed to read extensions folder.</p>';
      return;
    }

    let loadedCount = 0;

    for (const folder of folders) {
      const extDir = path.join(extensionsPath, folder);
      try {
        const stat = fs.statSync(extDir);
        if (!stat.isDirectory()) continue;

        const mainJsPath = path.join(extDir, 'src', 'main.js');
        if (fs.existsSync(mainJsPath)) {
          let extName = folder;
          let extDesc = 'No description provided';
          
          const pkgPath = path.join(extDir, 'package.json');
          if (fs.existsSync(pkgPath)) {
            try {
              const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
              if (pkg.name) extName = pkg.name;
              if (pkg.description) extDesc = pkg.description;
            } catch(e) {}
          }

          if (this.loadExtension(folder, extName, extDesc, mainJsPath)) {
            loadedCount++;
          }
        }
      } catch (err) {
        console.error(`Error loading extension ${folder}:`, err);
      }
    }

    if (loadedCount === 0 && listEl) {
      listEl.innerHTML = '<p style="padding: 10px; color: var(--text-secondary); text-align: center;">No active extensions found.</p>';
    }
  }

  loadExtension(folderName, name, desc, mainJsPath) {
    try {
      // Bust cache to allow reloading during dev
      delete window.nodeRequire.cache[window.nodeRequire.resolve(mainJsPath)];
      const extension = window.nodeRequire(mainJsPath);
      
      if (typeof extension.activate === 'function') {
        extension.activate(this.api);
        this.loadedExtensions.set(folderName, extension);
        console.log(`Successfully loaded extension: ${name}`);

        const listEl = document.getElementById('extensions-list');
        if (listEl) {
          const item = document.createElement('div');
          item.className = 'panel-section';
          item.style.padding = '10px';
          item.style.borderBottom = '1px solid var(--border-dark)';
          item.innerHTML = `
            <div style="font-weight: 600; color: var(--text-cyan); margin-bottom: 4px;">${name}</div>
            <div style="font-size: 12px; color: var(--text-secondary);">${desc}</div>
            <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Folder: ${folderName}</div>
          `;
          listEl.appendChild(item);
        }
        return true;
      } else {
        console.warn(`Extension ${name} does not export an 'activate' function.`);
        return false;
      }
    } catch (err) {
      console.error(`Failed to activate extension ${name}:`, err);
      return false;
    }
  }
}

module.exports = { ExtensionLoader };
