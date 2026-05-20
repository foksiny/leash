class UnleashingAPI {
  constructor(rendererContext) {
    this._ctx = rendererContext;
  }

  get ui() {
    return {
      addSidebarTab: (id, title, iconClass, drawerHtml) => {
        const sidebarTop = document.querySelector('.sidebar-top');
        const sidePanel = document.getElementById('side-panel');
        if (!sidebarTop || !sidePanel) return;

        // Add button
        const btn = document.createElement('button');
        btn.className = `sidebar-btn`;
        btn.id = `btn-${id}`;
        btn.title = title;
        if (iconClass) btn.classList.add(iconClass);
        sidebarTop.appendChild(btn);

        // Add drawer
        const section = document.createElement('section');
        section.className = 'drawer';
        section.id = `drawer-${id}`;
        section.innerHTML = drawerHtml;
        sidePanel.appendChild(section);

        // Hook up the event listener
        btn.addEventListener('click', () => {
          document.querySelectorAll('.sidebar-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');

          document.querySelectorAll('.drawer').forEach(d => {
            if (d.id === `drawer-${id}`) d.classList.add('active');
            else d.classList.remove('active');
          });
        });

        return { button: btn, drawer: section };
      },
      addButton: (containerId, htmlContent, onClick) => {
        const container = document.getElementById(containerId);
        if (!container) return null;
        const wrapper = document.createElement('span');
        wrapper.innerHTML = htmlContent;
        const btn = wrapper.firstElementChild;
        if (btn && onClick) {
          btn.addEventListener('click', onClick);
        }
        container.appendChild(btn);
        return btn;
      },
      modifyBackground: (cssVariables) => {
        const root = document.documentElement;
        for (const [key, value] of Object.entries(cssVariables)) {
          root.style.setProperty(key, value);
        }
      }
    };
  }

  get editor() {
    return {
      getActiveEditor: () => this._ctx.getEditorInstance(),
      onAutoComplete: (languageId, provider) => {
        if (window.monaco) {
          window.monaco.languages.registerCompletionItemProvider(languageId, provider);
        }
      },
      getActiveText: () => {
        const editor = this._ctx.getEditorInstance();
        return editor ? editor.getValue() : '';
      },
      insertText: (text) => {
        const editor = this._ctx.getEditorInstance();
        if (editor) {
          const position = editor.getPosition();
          editor.executeEdits("extension", [{
            range: new window.monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column),
            text: text,
            forceMoveMarkers: true
          }]);
        }
      }
    };
  }

  get theme() {
    return {
      registerTheme: (themeName, themeData) => {
        if (window.monaco) {
          window.monaco.editor.defineTheme(themeName, themeData);
          this._ctx.registerCustomTheme(themeName);
        }
      },
      setTheme: (themeName) => {
        if (window.monaco) {
          window.monaco.editor.setTheme(themeName);
        }
      }
    };
  }

  get languages() {
    return {
      registerLanguage: (langId, conf, languageDef) => {
        if (window.monaco) {
          window.monaco.languages.register({ id: langId });
          if (conf) window.monaco.languages.setLanguageConfiguration(langId, conf);
          if (languageDef) window.monaco.languages.setMonarchTokensProvider(langId, languageDef);
        }
      }
    };
  }

  get system() {
    return {
      overrideTabSystem: (handler) => {
        this._ctx.setTabOverride(handler);
      },
      getCoreState: () => {
        return this._ctx;
      }
    };
  }
}

module.exports = { UnleashingAPI };
