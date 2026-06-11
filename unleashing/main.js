const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let mainWindow;
let runningProcesses = new Map(); // Store active run/compile processes to allow stopping them

function killAllProcesses() {
  // Terminate any running leash compile/run processes
  for (let [id, proc] of runningProcesses.entries()) {
    try { proc.kill('SIGKILL'); } catch(e) {}
  }
  runningProcesses.clear();

  // Terminate the interactive terminal shell
  if (terminalShell) {
    try { terminalShell.kill('SIGKILL'); } catch(e) {}
    terminalShell = null;
  }
}

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

  mainWindow.on('close', function (e) {
    // Kill all child processes when the window is closing
    killAllProcesses();
  });

  mainWindow.on('closed', function () {
    mainWindow = null;
  });
}

app.on('ready', () => {
  createWindow();
});

app.on('before-quit', (event) => {
  // Prevent default quit to allow async cleanup
  if (runningProcesses.size > 0 || terminalShell) {
    event.preventDefault();
    killAllProcesses();
    // Now actually quit after cleanup
    app.quit();
  }
});

app.on('window-all-closed', function () {
  killAllProcesses();
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
      if (file === '.git' || file === 'node_modules' || file === '__pycache__' || file === 'out' || file.startsWith('.__temp_run_leash_exe_')) {
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
function detectLeashProject(workspacePath) {
  const uidePath = path.join(workspacePath, '.uide', 'project.json');
  if (!fs.existsSync(uidePath)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(uidePath, 'utf-8'));
    if (data.type === 'Leash Project') {
      const configPath = path.join(workspacePath, 'config.lshc');
      if (fs.existsSync(configPath)) {
        const config = {};
        const content = fs.readFileSync(configPath, 'utf-8');
        for (const line of content.split('\n')) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith('#')) continue;
          const commentIdx = trimmed.indexOf(' #');
          const clean = commentIdx >= 0 ? trimmed.slice(0, commentIdx).trim() : trimmed;
          const colonIdx = clean.indexOf(':');
          if (colonIdx === -1) continue;
          const key = clean.slice(0, colonIdx).trim();
          let val = clean.slice(colonIdx + 1).trim();
          if (val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
          config[key] = val;
        }
        return config;
      }
      return {};
    }
  } catch (e) {}
  return null;
}

ipcMain.on('execute-leash', (event, { commandId, action, filePath, args, optLevel, targetName, extraFlags, workspacePath }) => {
  const fileDir = path.dirname(filePath);
  const workspaceRoot = workspacePath || fileDir;
  
  // Choose standard python executable
  let pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  let cmdArgs = ['-m', 'leash.cli'];
  let cwd = fileDir;
  
  // Detect leash project context
  const projectConfig = detectLeashProject(workspaceRoot);
  const isLeashProject = projectConfig !== null;
  
  if (isLeashProject && (action === 'run' || action === 'compile')) {
    // Project-level commands
    cwd = workspaceRoot;
    cmdArgs.push(action === 'run' ? 'runp' : 'build');
    if (args && args.length > 0 && action === 'run') {
      cmdArgs.push('--', ...args);
    }
  } else {
    // File-level commands (existing behavior)
    if (action === 'check') {
      cmdArgs.push('check', filePath);
    } else if (action === 'run') {
      cmdArgs.push('run', filePath);
      if (checkLeashCliFlags(extraFlags, '--check')) cmdArgs.push('--check');
      if (checkLeashCliFlags(extraFlags, '--warnings-as-errors')) cmdArgs.push('--warnings-as-errors');
      if (optLevel) cmdArgs.push('--opt', optLevel);
      if (targetName) cmdArgs.push('--target', targetName);
      if (args && args.length > 0) cmdArgs.push(...args);
    } else if (action === 'compile') {
      cmdArgs.push('compile', filePath);
      let outputType = 'executable';
      let outputName = null;
      if (extraFlags) {
        if (extraFlags.includes('to-dynamic')) outputType = 'dynamic';
        else if (extraFlags.includes('to-static')) outputType = 'static';
        const toIndex = extraFlags.indexOf('to');
        if (toIndex !== -1 && toIndex + 1 < extraFlags.length) outputName = extraFlags[toIndex + 1];
      }
      if (outputType === 'dynamic') cmdArgs.push('to-dynamic');
      else if (outputType === 'static') cmdArgs.push('to-static');
      if (outputName) cmdArgs.push('to', outputName);
      if (checkLeashCliFlags(extraFlags, '--check')) cmdArgs.push('--check');
      if (checkLeashCliFlags(extraFlags, '--warnings-as-errors')) cmdArgs.push('--warnings-as-errors');
      if (optLevel) cmdArgs.push('--opt', optLevel);
      if (targetName) cmdArgs.push('--target', targetName);
    } else if (action === 'dump') {
      cmdArgs.push('dump', filePath);
      let outputName = null;
      if (extraFlags) {
        const toIndex = extraFlags.indexOf('to');
        if (toIndex !== -1 && toIndex + 1 < extraFlags.length) outputName = extraFlags[toIndex + 1];
      }
      if (outputName) cmdArgs.push('to', outputName);
      if (checkLeashCliFlags(extraFlags, '--check')) cmdArgs.push('--check');
      if (checkLeashCliFlags(extraFlags, '--warnings-as-errors')) cmdArgs.push('--warnings-as-errors');
      if (optLevel) cmdArgs.push('--opt', optLevel);
      if (targetName) cmdArgs.push('--target', targetName);
    }
  }

  // Add project import paths for file-level commands in a project context
  if (isLeashProject && action !== 'run' && action !== 'compile') {
    if (projectConfig && projectConfig.imports) {
      const importsPath = path.resolve(workspaceRoot, projectConfig.imports);
      if (fs.existsSync(importsPath)) {
        cmdArgs.push('--other-imports', importsPath);
      }
    }
  }

  // Spawn the child process
  const child = spawn(pythonCmd, cmdArgs, {
    cwd: cwd,
    env: { ...process.env, PYTHONUNBUFFERED: '1' }
  });
  
  runningProcesses.set(commandId, child);

  // Send stdout/stderr to the renderer in real time
  child.stdout.on('data', (data) => {
    if (!event.sender.isDestroyed()) event.sender.send(`proc-stdout-${commandId}`, data.toString());
  });

  child.stderr.on('data', (data) => {
    if (!event.sender.isDestroyed()) event.sender.send(`proc-stderr-${commandId}`, data.toString());
  });

  child.on('close', (code) => {
    runningProcesses.delete(commandId);
    if (!event.sender.isDestroyed()) event.sender.send(`proc-close-${commandId}`, code);
  });

  child.on('error', (err) => {
    runningProcesses.delete(commandId);
    if (!event.sender.isDestroyed()) {
      event.sender.send(`proc-stderr-${commandId}`, `Failed to start compiler: ${err.message}\nEnsure Python is installed and the 'leash' module is in python path.`);
      event.sender.send(`proc-close-${commandId}`, -1);
    }
  });
});

ipcMain.on('kill-process', (event, commandId) => {
  const child = runningProcesses.get(commandId);
  if (child) {
    try {
      child.kill();
      runningProcesses.delete(commandId);
      if (!event.sender.isDestroyed()) {
        event.sender.send(`proc-stderr-${commandId}`, `\n[Process terminated by user]\n`);
        event.sender.send(`proc-close-${commandId}`, -2);
      }
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
    if (!event.sender.isDestroyed()) event.sender.send('terminal-output', data.toString());
  });

  terminalShell.stderr.on('data', (data) => {
    if (!event.sender.isDestroyed()) event.sender.send('terminal-output', data.toString());
  });

  terminalShell.on('close', (code) => {
    if (!event.sender.isDestroyed()) event.sender.send('terminal-output', `\n[Terminal exited with code ${code}]\n`);
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

// --- PROJECT CREATION ---
const os = require('os');

ipcMain.handle('get-home-dir', () => {
  return os.homedir();
});

ipcMain.handle('select-uie-file', async () => {
  const result = dialog.showOpenDialogSync(mainWindow, {
    properties: ['openFile'],
    filters: [
      { name: 'ZIP Archive', extensions: ['zip'] },
      { name: 'All Files', extensions: ['*'] }
    ]
  });
  if (result && result.length > 0) {
    return result[0];
  }
  return null;
});

ipcMain.handle('save-uie-dialog', async () => {
  const result = dialog.showSaveDialogSync(mainWindow, {
    filters: [
      { name: 'ZIP Archive', extensions: ['zip'] }
    ],
    defaultPath: path.join(os.homedir(), 'UnleashingProjects', 'extension.zip')
  });
  return result || null;
});

ipcMain.handle('create-project', async (event, { projectType, projectName }) => {
  try {
    const projectsDir = path.join(os.homedir(), 'UnleashingProjects');
    if (!fs.existsSync(projectsDir)) {
      fs.mkdirSync(projectsDir, { recursive: true });
    }

    const projectPath = path.join(projectsDir, projectName);
    if (fs.existsSync(projectPath)) {
      return { success: false, error: 'Project folder already exists' };
    }

    fs.mkdirSync(projectPath, { recursive: true });
    fs.mkdirSync(path.join(projectPath, 'src'), { recursive: true });
    
    // Create .uide configuration
    fs.mkdirSync(path.join(projectPath, '.uide'), { recursive: true });
    const uideType = projectType === 'leash' ? 'Leash Project' : 'Unleashing IDE Extension';
    fs.writeFileSync(path.join(projectPath, '.uide', 'project.json'), JSON.stringify({
      name: projectName,
      type: uideType
    }, null, 2), 'utf-8');

    if (projectType === 'leash') {
      fs.mkdirSync(path.join(projectPath, 'imports'), { recursive: true });
      fs.mkdirSync(path.join(projectPath, 'out'), { recursive: true });
      fs.writeFileSync(path.join(projectPath, 'src', 'main.lsh'), 'fnc main |> show("Hello, World!");\n', 'utf-8');
      fs.writeFileSync(path.join(projectPath, 'config.lshc'), [
        'main: "src/main.lsh"',
        'clibs: {}',
        'imports: "imports/"',
        'opt_level: "O3"',
        `out_name: "${projectName}"`,
        ''
      ].join('\n'), 'utf-8');
      fs.writeFileSync(path.join(projectPath, 'project.json'), JSON.stringify({
        name: projectName,
        type: "executable",
        version: "1.0.0"
      }, null, 2), 'utf-8');
    } else if (projectType === 'extension') {
      fs.mkdirSync(path.join(projectPath, 'assets'), { recursive: true });
      fs.writeFileSync(path.join(projectPath, 'src', 'main.js'), `module.exports = {
  activate: (api) => {
    console.log("Extension activated!");
    // Example: api.ui.addButton('tabs-bar', '<button>Hello</button>');
  }
};
`, 'utf-8');
      fs.writeFileSync(path.join(projectPath, 'package.json'), JSON.stringify({
        name: projectName,
        version: "1.0.0",
        description: "An Unleashing IDE extension"
      }, null, 2), 'utf-8');
      fs.writeFileSync(path.join(projectPath, 'README.md'), `# ${projectName}\n\nA new Unleashing IDE extension.`, 'utf-8');
      fs.writeFileSync(path.join(projectPath, '.gitignore'), `node_modules/\n`, 'utf-8');
    } else {
      return { success: false, error: 'Unknown project type' };
    }

    return { success: true, projectPath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('get-projects', async () => {
  try {
    const projectsDir = path.join(os.homedir(), 'UnleashingProjects');
    if (!fs.existsSync(projectsDir)) {
      return [];
    }

    const folders = fs.readdirSync(projectsDir);
    const projects = [];

    for (const folder of folders) {
      const pPath = path.join(projectsDir, folder);
      try {
        if (!fs.statSync(pPath).isDirectory()) continue;
        
        const uidePath = path.join(pPath, '.uide', 'project.json');
        let type = "Unknown";
        if (fs.existsSync(uidePath)) {
          try {
            const data = JSON.parse(fs.readFileSync(uidePath, 'utf-8'));
            if (data.type) type = data.type;
          } catch(e) {}
        }
        projects.push({ name: folder, path: pPath, type });
      } catch(e) {}
    }
    return projects;
  } catch (err) {
    console.error(err);
    return [];
  }
});

ipcMain.handle('install-extension', async (event, sourceFolder) => {
  try {
    const extName = path.basename(sourceFolder);
    const destFolder = path.join(os.homedir(), '.UnleashingExtensions', extName);
    
    if (!fs.existsSync(path.join(os.homedir(), '.UnleashingExtensions'))) {
      fs.mkdirSync(path.join(os.homedir(), '.UnleashingExtensions'), { recursive: true });
    }

    // copy folder over
    fs.cpSync(sourceFolder, destFolder, { recursive: true, force: true });
    return { success: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('move-path', async (event, { sourcePath, destPath }) => {
  try {
    if (fs.existsSync(destPath)) {
      return { success: false, error: 'Target path already exists' };
    }
    const destDir = path.dirname(destPath);
    if (!fs.existsSync(destDir)) {
      fs.mkdirSync(destDir, { recursive: true });
    }
    fs.renameSync(sourcePath, destPath);
    return { success: true, newPath: destPath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

const { execSync } = require('child_process');

ipcMain.handle('extract-uie', async (event, { uieFilePath, destFolder }) => {
  try {
    if (!fs.existsSync(destFolder)) {
      fs.mkdirSync(destFolder, { recursive: true });
    }

    // Use PowerShell's Expand-Archive on Windows, unzip on other platforms
    if (process.platform === 'win32') {
      execSync(`powershell -Command "Expand-Archive -Path '${uieFilePath.replace(/'/g, "''")}' -DestinationPath '${destFolder.replace(/'/g, "''")}' -Force"`, {
        stdio: 'pipe'
      });
    } else {
      execSync(`unzip -o '${uieFilePath}' -d '${destFolder}'`, { stdio: 'pipe' });
    }

    return { success: true, destFolder };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('pack-uie', async (event, { sourceFolder, outputPath }) => {
  try {
    // Use PowerShell's Compress-Archive on Windows, zip on other platforms
    if (process.platform === 'win32') {
      execSync(`powershell -Command "Compress-Archive -Path '${(path.join(sourceFolder, '*')).replace(/'/g, "''")}' -DestinationPath '${outputPath.replace(/'/g, "''")}' -Force"`, {
        stdio: 'pipe'
      });
    } else {
      execSync(`cd '${sourceFolder}' && zip -r '${outputPath}' .`, { stdio: 'pipe' });
    }

    return { success: true, outputPath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});
