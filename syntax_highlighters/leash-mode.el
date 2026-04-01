;;; leash-mode.el --- Major mode for Leash (.lsh)

;;; Commentary:
;; Major mode for Leash, a strongly-typed modern language.

;;; Code:

(defvar leash-mode-hook nil)

(defvar leash-mode-map
  (let ((map (make-keymap)))
    (define-key map (kbd "C-j") 'newline-and-indent)
    map)
  "Keymap for Leash major mode")

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.lsh\\'" . leash-mode))

(defconst leash-font-lock-keywords
  (list
   '("\\b\\(fnc\\|def\\|struct\\|union\\|enum\\|class\\|type\\|template\\|return\\|if\\|also\\|else\\|while\\|for\\|do\\|foreach\\|in\\|imut\\|vec\\|vector\\|this\\|pub\\|priv\\|static\\|stop\\|continue\\|use\\)\\b" . font-lock-keyword-face)
   '("\\b\\(int\\|uint\\|float\\|bool\\|string\\|char\\|void\\|array\\|vec\\)\\b\\(?:<[0-9]+>\\)?" . font-lock-type-face)
   '("\\b\\(true\\|false\\|null\\)\\b" . font-lock-constant-face)
   '("\\b\\(show\\|get\\|set\\|toint\\|tofloat\\|tostring\\|cstr\\|lstr\\|size\\|cur\\|name\\|pushb\\|popb\\|pushf\\|popf\\|insert\\|clear\\|rand\\|randf\\|seed\\|choose\\|wait\\|timepass\\|exit\\)\\b" . font-lock-builtin-face)
   '("&&\\|||\\|<<\\|>>\\|[+\\-*/%&|^~!<>=]=?" . font-lock-variable-name-face)
   '("\\(&\\|\\*\\|->\\)" . font-lock-type-face)
   '("::" . font-lock-special-form-face)
   ;; Class inheritance syntax: class(Parent)
   '("\\bclass\\s*(\\s*\\([A-Z][a-zA-Z0-9_]*\\)\\s*)" . font-lock-type-face)
   ;; Type annotation with parent: : Type(Parent)
   '(":\\s*\\([A-Z][a-zA-Z0-9_]*\\)\\s*(\\s*\\([A-Z][a-zA-Z0-9_]*\\)\\s*)" . font-lock-type-face)
   '("\\(?:<\\|,\\)[ \t]*\\([A-Z][a-zA-Z0-9_]*\\)" . (1 font-lock-type-face))
   )
  "Default font-lock keywords for Leash mode")

(defvar leash-syntax-table
  (let ((st (make-syntax-table)))
    ;; Comments //
    (modify-syntax-entry ?/ ". 12" st)
    (modify-syntax-entry ?\n ">" st)
    ;; Strings
    (modify-syntax-entry ?\" "\"" st)
    (modify-syntax-entry ?' "\"" st)
    st)
  "Syntax table for leash-mode")

;;;###autoload
(define-derived-mode leash-mode prog-mode "Leash"
  "Major mode for editing Leash source files."
  :syntax-table leash-syntax-table
  (setq font-lock-defaults '((leash-font-lock-keywords))))

(provide 'leash-mode)

;;; leash-mode.el ends here
