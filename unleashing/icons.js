// SVG Icons Library for Leash Studio IDE
// High performance, pure SVG paths loaded directly into DOM

const Icons = {
  // Minimalistic Literal Leash Icon (Handle -> Wavy Curve -> Snap Hook)
  leashFile: `<svg viewBox="0 0 24 24" fill="none" stroke="#00f0ff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <!-- Leash loop handle at the top left -->
    <path d="M5 8c0-2.2 1.8-4 4-4s4 1.8 4 4c0 3-4 5-4 7" stroke-dasharray="20 0" />
    <!-- Styled wavy cord connecting handle to hook -->
    <path d="M9 15c0 2 2 3 4 3s4-2 4-4c0-2-1.5-3-3-3s-3.5 2-3.5 4" />
    <!-- Metal snap hook/latch at bottom right -->
    <rect x="15" y="16" width="3" height="5" rx="1.5" stroke="#8c9cb8" stroke-width="1.5" />
    <path d="M16.5 16v-2" stroke="#8c9cb8" stroke-width="1.5" />
  </svg>`,

  // Closed Folder Icon
  folder: `<svg viewBox="0 0 24 24" fill="none" stroke="#ffd000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
  </svg>`,

  // Open Folder Icon
  folderOpen: `<svg viewBox="0 0 24 24" fill="none" stroke="#ffd000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M6 2h12a2 2 0 0 1 2 2v4H4V4a2 2 0 0 1 2-2z" />
    <path d="M2 10h20l-2 10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2L2 10z" />
  </svg>`,

  // JSON File Icon (Cyan Curly Brackets)
  jsonFile: `<svg viewBox="0 0 24 24" fill="none" stroke="#00f0ff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="#8c9cb8" />
    <path d="M14 2v6h6" stroke="#8c9cb8" />
    <!-- Small elegant { } brackets -->
    <path d="M8 12c.5 0 1-.5 1-1v-1c0-.5.5-1 1-1m0 6c-.5 0-1-.5-1-1v-1c0-.5-.5-1-1-1" stroke="#00f0ff" stroke-width="1.5" />
    <path d="M16 12c-.5 0-1-.5-1-1v-1c0-.5-.5-1-1-1m0 6c.5 0 1-.5 1-1v-1c0-.5.5-1 1-1" stroke="#00f0ff" stroke-width="1.5" />
  </svg>`,

  // Plain Text / Generic File Icon
  textFile: `<svg viewBox="0 0 24 24" fill="none" stroke="#8c9cb8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6" />
    <line x1="16" y1="13" x2="8" y2="13" stroke-width="1.5" />
    <line x1="16" y1="17" x2="8" y2="17" stroke-width="1.5" />
    <line x1="10" y1="9" x2="8" y2="9" stroke-width="1.5" />
  </svg>`,

  // Explorer Tab Sidebar Icon
  explorer: `<svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/></svg>`,

  // Run & Debug Sidebar Icon
  debug: `<svg viewBox="0 0 24 24"><path d="M19 8h-1.81c-.45-.78-1.07-1.45-1.82-1.96L17 4.41 15.59 3l-2.17 2.17C12.9 5.06 12.46 5 12 5c-.46 0-.9.06-1.41.17L8.41 3 7 4.41l1.62 1.63C7.88 6.55 7.26 7.22 6.81 8H5v2h1.09c-.05.33-.09.66-.09 1v1H5v2h1v1c0 .34.04.67.09 1H5v2h1.81c.9 1.56 2.58 2.62 4.51 2.76L12 21h.09c1.93-.14 3.61-1.2 4.51-2.76H19v-2h-1.09c.05-.33.09-.66.09-1v-1h1v-2h-1v-1c0-.34-.04-.67-.09-1H19V8zm-3 6c0 .34-.04.67-.09 1H8.09c-.05-.33-.09-.66-.09-1v-1h8v1zm0-3H8v-1c0-.34.04-.67.09-1h7.82c.05.33.09.66.09 1v1z"/></svg>`,

  // Settings Sidebar Icon
  settings: `<svg viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>`,

  // Command Palette Sidebar Icon
  palette: `<svg viewBox="0 0 24 24"><path d="M12 2C6.49 2 2 6.49 2 12s4.49 10 10 10c1.25 0 2.06-1.04 1.83-2.22-.09-.45-.09-.9.09-1.25.26-.54.72-.82 1.34-.82h1.61c2.93 0 5.13-2.42 5.13-5.26C22 6.49 17.51 2 12 2zm-5 9c-.83 0-1.5-.67-1.5-1.5S6.17 8 7 8s1.5.67 1.5 1.5S7.83 11 7 11zm3-3c-.83 0-1.5-.67-1.5-1.5S9.17 5 10 5s1.5.67 1.5 1.5S10.83 8 10 8zm4 0c-.83 0-1.5-.67-1.5-1.5S13.17 5 14 5s1.5.67 1.5 1.5S14.83 8 14 8zm3 3c-.83 0-1.5-.67-1.5-1.5S16.17 8 17 8s1.5.67 1.5 1.5S17.83 11 17 11z"/></svg>`,

  // New File Icon
  newFile: `<svg viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 14h-3v3h-2v-3H8v-2h3v-3h2v3h3v2zm-3-7V3.5L18.5 9H13z"/></svg>`,

  // New Folder Icon
  newFolder: `<svg viewBox="0 0 24 24"><path d="M20 6h-8l-2-2H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z"/></svg>`,

  // Refresh Icon
  refresh: `<svg viewBox="0 0 24 24"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>`,

  // Close Folder Icon
  closeFolder: `<svg viewBox="0 0 24 24"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-1 11H5V8h14v9zm-1.8-6.8L15.8 9 12 12.8 8.2 9 7 10.2l3.8 3.8L7 17.8 8.2 19l3.8-3.8 3.8 3.8 1.2-1.2-3.8-3.8z"/></svg>`,

  // Play Run Icon (Cyan play button)
  runPlay: `<svg viewBox="0 0 24 24" fill="#00f0ff"><path d="M8 5v14l11-7z"/></svg>`,

  // Cog Build Icon (Blue cogwheel)
  runBuild: `<svg viewBox="0 0 24 24" fill="#005eff"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.3C.5 6.7.9 9.8 2.9 11.8c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.6z"/></svg>`,

  // Dump LLVM IR Icon (Silver document with arrow)
  runDump: `<svg viewBox="0 0 24 24" fill="none" stroke="#8c9cb8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="12" y1="18" x2="12" y2="12" />
    <polyline points="9 15 12 18 15 15" />
  </svg>`,

  // Stop Process Icon
  runStop: `<svg viewBox="0 0 24 24" fill="#ff5c6c"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`,

  // Clear Terminal Icon
  clear: `<svg viewBox="0 0 24 24"><path d="M15 16h4v2h-4zm0-8h7v2h-7zm0-4h7v2h-7zm-4 10V8H3v8h8zm-1-6H4v4h6V8z"/></svg>`,

  // Toggle/Minimize Panel Icon
  minimizePanel: `<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>`,

  // Arrow Right Node
  arrowRight: `<svg viewBox="0 0 24 24"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>`,

  // Close Tab Button (x mark)
  closeTab: `<svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`,

  // Checkmark status ok
  checkOk: `<svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>`,

  // Problem Warning Icon
  warn: `<svg viewBox="0 0 24 24" fill="#ffd000"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>`,

  // Problem Error Icon
  error: `<svg viewBox="0 0 24 24" fill="#ff5c6c"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>`
};

// Bind icons to document loads
document.addEventListener("DOMContentLoaded", () => {
  // Inject app header icon
  const appIconEl = document.getElementById("app-icon");
  if (appIconEl) appIconEl.innerHTML = Icons.leashFile;

  // Inject sidebar buttons SVG icons
  const injectSidebarIcon = (id, svgKey) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = Icons[svgKey];
  };

  injectSidebarIcon("btn-explorer", "explorer");
  injectSidebarIcon("btn-debug", "debug");
  injectSidebarIcon("btn-settings", "settings");
  injectSidebarIcon("btn-palette", "palette");

  // Inject action buttons
  const injectActionIcon = (id, svgKey) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = Icons[svgKey];
  };

  injectActionIcon("action-new-file", "newFile");
  injectActionIcon("action-new-folder", "newFolder");
  injectActionIcon("action-refresh", "refresh");
  injectActionIcon("action-close-folder", "closeFolder");
  injectActionIcon("action-clear-console", "clear");
  injectActionIcon("action-toggle-console", "minimizePanel");

  // Inject Run buttons
  injectActionIcon("icon-run-play", "runPlay");
  injectActionIcon("icon-run-build", "runBuild");
  injectActionIcon("icon-run-dump", "runDump");
  injectActionIcon("icon-run-stop", "runStop");
  
  // Inject splash logo
  const splashLogo = document.getElementById("splash-logo");
  if (splashLogo) splashLogo.innerHTML = Icons.leashFile;
});

// Expose icons for tree rendering
if (typeof module !== 'undefined') {
  module.exports = Icons;
}
