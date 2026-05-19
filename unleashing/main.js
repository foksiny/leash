const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let mainWindow;
let runningProcesses = new Map(); // Store active run/compile processes to allow stopping them

function createWindow() {
  // Look for icon file (supports .png, .ico on Windows)
  const iconPath = path.join(__dirname, 'icon.png');
  const icoPath = path.join(__dirname, 'icon.ico');

  let windowIcon = undefined;
  if (fs.existsSync(iconPath)) {
    windowIcon = iconPath;
  } else if (fs.existsSync(icoPath)) {
    windowIcon = icoPath;
  }

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    frame: false, // Frameless window for premium, custom VS Code-like header bar
    backgroundColor: '#060813',
    icon: windowIcon,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false, // Allows easy direct access to Electron APIs in our high-perf UI
      enableRemoteModule: true
    }
  });

  mainWindow.loadFile('index.html');

  // Open devtools in development if needed
  // mainWindow.webContents.openDevTools();

  mainWindow.on('closed', function () {
    mainWindow = null;
  });
}

app.on('ready', () => {
  createWindow();
});

app.on('window-all-closed', function () {
  // Terminate any leftover processes
  for (let [id, proc] of runningProcesses.entries()) {
    try { proc.kill(); } catch(e) {}
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', function () {
  if (mainWindow === null) {
    createWindow();
  }
});

// --- CUSTOM WINDOW CONTROLS ---
ipcMain.on('window-minimize', () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-maximize', () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.on('window-close', () => {
  if (mainWindow) mainWindow.close();
});

// --- WORKSPACE & FILE OPERATIONS ---
ipcMain.handle('select-folder', async () => {
  const result = dialog.showOpenDialogSync(mainWindow, {
    properties: ['openDirectory']
  });
  if (result && result.length > 0) {
    return result[0];
  }
  return null;
});

// Scan folder recursively with performance filters (ignoring node_modules, git, caching results)
function scanDirectory(dirPath, rootPath) {
  const items = [];
  try {
    const files = fs.readdirSync(dirPath);
    for (const file of files) {
      // Ignore compiled binaries, caches, and dependency folders to minimize lag
      if (file === '.git' || file === 'node_modules' || file === '__pycache__' || file.startsWith('.__temp_run_leash_exe_')) {
        continue;
      }
      
      const fullPath = path.join(dirPath, file);
      const relativePath = path.relative(rootPath, fullPath);
      let stat;
      try {
        stat = fs.statSync(fullPath);
      } catch (e) {
        continue; // Skip inaccessible files
      }

      if (stat.isDirectory()) {
        items.push({
          name: file,
          path: fullPath,
          relativePath: relativePath,
          isDirectory: true,
          children: scanDirectory(fullPath, rootPath) // Deep scan
        });
      } else {
        items.push({
          name: file,
          path: fullPath,
          relativePath: relativePath,
          isDirectory: false,
          size: stat.size
        });
      }
    }
  } catch (err) {
    console.error("Error reading directory:", dirPath, err);
  }
  
  // Sort: directories first, then files alphabetically
  return items.sort((a, b) => {
    if (a.isDirectory && !b.isDirectory) return -1;
    if (!a.isDirectory && b.isDirectory) return 1;
    return a.name.localeCompare(b.name);
  });
}

ipcMain.handle('get-file-tree', async (event, dirPath) => {
  if (!dirPath || !fs.existsSync(dirPath)) return [];
  return scanDirectory(dirPath, dirPath);
});

ipcMain.handle('read-file', async (event, filePath) => {
  try {
    if (fs.existsSync(filePath)) {
      return { success: true, content: fs.readFileSync(filePath, 'utf-8') };
    }
    return { success: false, error: 'File does not exist' };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('write-file', async (event, { filePath, content }) => {
  try {
    fs.writeFileSync(filePath, content, 'utf-8');
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('create-file', async (event, { folderPath, fileName }) => {
  try {
    const fullPath = path.join(folderPath, fileName);
    if (fs.existsSync(fullPath)) {
      return { success: false, error: 'File already exists' };
    }
    fs.writeFileSync(fullPath, '', 'utf-8');
    return { success: true, filePath: fullPath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('create-folder', async (event, { folderPath, folderName }) => {
  try {
    const fullPath = path.join(folderPath, folderName);
    if (fs.existsSync(fullPath)) {
      return { success: false, error: 'Folder already exists' };
    }
    fs.mkdirSync(fullPath, { recursive: true });
    return { success: true, folderPath: fullPath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('delete-path', async (event, targetPath) => {
  try {
    if (fs.existsSync(targetPath)) {
      const stat = fs.statSync(targetPath);
      if (stat.isDirectory()) {
        fs.rmSync(targetPath, { recursive: true, force: true });
      } else {
        fs.unlinkSync(targetPath);
      }
      return { success: true };
    }
    return { success: false, error: 'Path does not exist' };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('rename-path', async (event, { oldPath, newName }) => {
  try {
    const dir = path.dirname(oldPath);
    const newPath = path.join(dir, newName);
    if (fs.existsSync(newPath)) {
      return { success: false, error: 'Target name already exists' };
    }
    fs.renameSync(oldPath, newPath);
    return { success: true, newPath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

// --- LEASH PROCESS EXECUTION (RUN/COMPILE/CHECK) ---
ipcMain.on('execute-leash', (event, { commandId, action, filePath, args, optLevel, targetName, extraFlags }) => {
  const workspaceRoot = path.dirname(filePath);
  
  // Choose standard python executable
  let pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  
  let cmdArgs = ['-m', 'leash.cli'];
  
  if (action === 'check') {
    cmdArgs.push('check', filePath);
  } else if (action === 'run') {
    cmdArgs.push('run', filePath);
    if (checkLeashCliFlags(extraFlags, '--check')) cmdArgs.push('--check');
    if (checkLeashCliFlags(extraFlags, '--warnings-as-errors')) cmdArgs.push('--warnings-as-errors');
    if (optLevel) cmdArgs.push('--opt', optLevel);
    if (targetName) cmdArgs.push('--target', targetName);
    
    // Append program arguments
    if (args && args.length > 0) {
      cmdArgs.push(...args);
    }
  } else if (action === 'compile') {
    cmdArgs.push('compile', filePath);
    
    // Extract output compile flags
    let outputType = 'executable';
    let outputName = null;
    
    if (extraFlags) {
      if (extraFlags.includes('to-dynamic')) outputType = 'dynamic';
      else if (extraFlags.includes('to-static')) outputType = 'static';
      
      const toIndex = extraFlags.indexOf('to');
      if (toIndex !== -1 && toIndex + 1 < extraFlags.length) {
        outputName = extraFlags[toIndex + 1];
      }
    }
    
    if (outputType === 'dynamic') cmdArgs.push('to-dynamic');
    else if (outputType === 'static') cmdArgs.push('to-static');
    
    if (outputName) {
      cmdArgs.push('to', outputName);
    }
    
    if (checkLeashCliFlags(extraFlags, '--check')) cmdArgs.push('--check');
    if (checkLeashCliFlags(extraFlags, '--warnings-as-errors')) cmdArgs.push('--warnings-as-errors');
    if (optLevel) cmdArgs.push('--opt', optLevel);
    if (targetName) cmdArgs.push('--target', targetName);
  } else if (action === 'dump') {
    cmdArgs.push('dump', filePath);
    
    let outputName = null;
    if (extraFlags) {
      const toIndex = extraFlags.indexOf('to');
      if (toIndex !== -1 && toIndex + 1 < extraFlags.length) {
        outputName = extraFlags[toIndex + 1];
      }
    }
    
    if (outputName) {
      cmdArgs.push('to', outputName);
    }
    
    if (checkLeashCliFlags(extraFlags, '--check')) cmdArgs.push('--check');
    if (checkLeashCliFlags(extraFlags, '--warnings-as-errors')) cmdArgs.push('--warnings-as-errors');
    if (optLevel) cmdArgs.push('--opt', optLevel);
    if (targetName) cmdArgs.push('--target', targetName);
  }

  // Spawn the child process
  const child = spawn(pythonCmd, cmdArgs, {
    cwd: workspaceRoot,
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });
  
  runningProcesses.set(commandId, child);

  // Send stdout/stderr to the renderer in real time
  child.stdout.on('data', (data) => {
    event.sender.send(`proc-stdout-${commandId}`, data.toString());
  });

  child.stderr.on('data', (data) => {
    event.sender.send(`proc-stderr-${commandId}`, data.toString());
  });

  child.on('close', (code) => {
    runningProcesses.delete(commandId);
    event.sender.send(`proc-close-${commandId}`, code);
  });

  child.on('error', (err) => {
    runningProcesses.delete(commandId);
    event.sender.send(`proc-stderr-${commandId}`, `Failed to start compiler: ${err.message}\nEnsure Python is installed and the 'leash' module is in python path.`);
    event.sender.send(`proc-close-${commandId}`, -1);
  });
});

ipcMain.on('kill-process', (event, commandId) => {
  const child = runningProcesses.get(commandId);
  if (child) {
    try {
      child.kill();
      runningProcesses.delete(commandId);
      event.sender.send(`proc-stderr-${commandId}`, `\n[Process terminated by user]\n`);
      event.sender.send(`proc-close-${commandId}`, -2);
    } catch (e) {
      console.error("Failed to kill process:", e);
    }
  }
});

function checkLeashCliFlags(flagsArray, flag) {
  return flagsArray && flagsArray.includes(flag);
}

// --- PERSISTENT INTERACTIVE POWERSHELL TERMINAL ---
let terminalShell = null;

ipcMain.on('spawn-terminal', (event, { cwd }) => {
  if (terminalShell) {
    try { terminalShell.kill(); } catch (e) {}
  }

  let shellCmd = process.platform === 'win32' ? 'powershell.exe' : 'bash';
  let shellArgs = process.platform === 'win32' ? ['-NoLogo', '-NoExit'] : [];

  terminalShell = spawn(shellCmd, shellArgs, {
    cwd: cwd || process.cwd(),
    env: { ...process.env, PSModulePath: '' } 
  });

  terminalShell.stdout.on('data', (data) => {
    event.sender.send('terminal-output', data.toString());
  });

  terminalShell.stderr.on('data', (data) => {
    event.sender.send('terminal-output', data.toString());
  });

  terminalShell.on('close', (code) => {
    event.sender.send('terminal-output', `\n[Terminal exited with code ${code}]\n`);
    terminalShell = null;
  });
});

ipcMain.on('terminal-input', (event, input) => {
  if (terminalShell) {
    try {
      terminalShell.stdin.write(input);
    } catch(e) {
      console.error("Failed to write to terminal stdin:", e);
    }
  }
});

ipcMain.on('process-input', (event, { commandId, input }) => {
  const child = runningProcesses.get(commandId);
  if (child) {
    try {
      child.stdin.write(input);
    } catch(e) {
      console.error("Failed to write to process stdin:", e);
    }
  }
});
