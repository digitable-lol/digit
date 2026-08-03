import { contextBridge, ipcRenderer, webUtils } from 'electron'

contextBridge.exposeInMainWorld('digitDesktop', {
  getConnection: profile => ipcRenderer.invoke('digit:connection', profile),
  revalidateConnection: () => ipcRenderer.invoke('digit:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('digit:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('digit:gateway:ws-url', profile),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('digit:window:openSession', sessionId, opts),
  openWindow: () => ipcRenderer.invoke('digit:window:openInstance'),
  claimAmbientCue: key => ipcRenderer.invoke('digit:ambient:claim', key),
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('digit:wake-indicator:get'),
    setState: state => ipcRenderer.send('digit:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('digit:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('digit:wake-indicator:state', listener)
    }
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('digit:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('digit:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('digit:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('digit:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('digit:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('digit:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('digit:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('digit:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('digit:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('digit:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('digit:pet-overlay:control', listener)
    }
  },
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('digit:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('digit:quick-entry:settings:set', patch),
    submit: payload => ipcRenderer.send('digit:quick-entry:submit', payload),
    dismiss: () => ipcRenderer.send('digit:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('digit:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('digit:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('digit:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('digit:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('digit:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('digit:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('digit:quick-entry:shown', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('digit:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('digit:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('digit:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('digit:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('digit:connection-config:test', payload),
  sshConfigHosts: () => ipcRenderer.invoke('digit:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('digit:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('digit:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('digit:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('digit:connection-config:oauth-logout', remoteUrl),
  // Digit Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('digit:cloud:status'),
    login: () => ipcRenderer.invoke('digit:cloud:login'),
    logout: () => ipcRenderer.invoke('digit:cloud:logout'),
    discover: org => ipcRenderer.invoke('digit:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('digit:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    get: () => ipcRenderer.invoke('digit:profile:get'),
    set: name => ipcRenderer.invoke('digit:profile:set', name)
  },
  api: request => ipcRenderer.invoke('digit:api', request),
  notify: payload => ipcRenderer.invoke('digit:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('digit:requestMicrophoneAccess'),
  readFileDataUrl: filePath => ipcRenderer.invoke('digit:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('digit:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('digit:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('digit:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('digit:readFileText', filePath),
  selectPaths: options => ipcRenderer.invoke('digit:selectPaths', options),
  writeClipboard: text => ipcRenderer.invoke('digit:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('digit:readClipboard'),
  saveImageFromUrl: url => ipcRenderer.invoke('digit:saveImageFromUrl', url),
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('digit:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('digit:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('digit:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('digit:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('digit:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('digit:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('digit:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('digit:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('digit:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('digit:translucency', payload),
  setKeepAwake: on => ipcRenderer.send('digit:keep-awake', on),
  setPreviewShortcutActive: active => ipcRenderer.send('digit:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('digit:openExternal', url),
  openPreviewInBrowser: url => ipcRenderer.invoke('digit:openPreviewInBrowser', url),
  fetchLinkTitle: url => ipcRenderer.invoke('digit:fetchLinkTitle', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('digit:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('digit:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('digit:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('digit:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('digit:zoom:get'),
    setPercent: percent => ipcRenderer.send('digit:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('digit:zoom:changed', listener)

      return () => ipcRenderer.removeListener('digit:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('digit:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('digit:logs:recent'),
  readDir: dirPath => ipcRenderer.invoke('digit:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('digit:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('digit:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('digit:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('digit:fs:desktopPluginsRoot'),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('digit:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('digit:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('digit:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('digit:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('digit:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('digit:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('digit:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('digit:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('digit:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('digit:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('digit:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('digit:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('digit:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('digit:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('digit:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('digit:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('digit:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('digit:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('digit:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('digit:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('digit:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('digit:git:review:shipInfo', repoPath),
      createPr: repoPath => ipcRenderer.invoke('digit:git:review:createPr', repoPath)
    }
  },
  terminal: {
    cwd: id => ipcRenderer.invoke('digit:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('digit:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('digit:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('digit:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('digit:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `digit:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `digit:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('digit:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('digit:close-preview-requested', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('digit:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('digit:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('digit:open-updates', listener)

    return () => ipcRenderer.removeListener('digit:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('digit:deep-link', listener)

    return () => ipcRenderer.removeListener('digit:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('digit:deep-link-ready'),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('digit:window-state-changed', listener)

    return () => ipcRenderer.removeListener('digit:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('digit:focus-session', listener)

    return () => ipcRenderer.removeListener('digit:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('digit:notification-action', listener)

    return () => ipcRenderer.removeListener('digit:notification-action', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('digit:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('digit:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('digit:backend-exit', listener)

    return () => ipcRenderer.removeListener('digit:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('digit:connection:applied', listener)

    return () => ipcRenderer.removeListener('digit:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('digit:power-resume', listener)

    return () => ipcRenderer.removeListener('digit:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('digit:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('digit:power-battery', listener)

    return () => ipcRenderer.removeListener('digit:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('digit:boot-progress', listener)

    return () => ipcRenderer.removeListener('digit:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('digit:bootstrap:get'),
  continueBootstrapLocal: () => ipcRenderer.invoke('digit:bootstrap:continue-local'),
  resetBootstrap: () => ipcRenderer.invoke('digit:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('digit:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('digit:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('digit:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('digit:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('digit:version'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('digit:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('digit:uninstall:summary'),
    run: mode => ipcRenderer.invoke('digit:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('digit:updates:check'),
    apply: opts => ipcRenderer.invoke('digit:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('digit:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('digit:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('digit:updates:progress', listener)

      return () => ipcRenderer.removeListener('digit:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('digit:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('digit:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('digit:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('digit:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('digit:found-in-page', listener)

    return () => ipcRenderer.removeListener('digit:found-in-page', listener)
  }
})
