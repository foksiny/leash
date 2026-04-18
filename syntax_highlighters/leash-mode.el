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

(defun leash-mode-indent-setup ()
  "Setup indentation for Leash mode."
  (setq-local indent-tabs-mode nil)
  (setq-local tab-width 4)
  (setq-local leash-indent-level 4))

(add-hook 'leash-mode-hook #'leash-mode-indent-setup)

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.lsh\\'" . leash-mode))

(defconst leash-font-lock-keywords
  (list
   '("\\b\\(fnc\\|def\\|struct\\|union\\|enum\\|class\\|type\\|template\\|return\\|if\\|also\\|else\\|while\\|for\\|do\\|foreach\\|in\\|imut\\|vec\\|vector\\|this\\|pub\\|priv\\|static\\|stop\\|continue\\|use\\|works\\|otherwise\\|switch\\|case\\|default\\|unsafe\\|as\\|inline\\|defer\\)\\b" . font-lock-keyword-face)
   '("\\b\\(int\\|uint\\|float\\|bool\\|string\\|char\\|void\\|array\\|vec\\)\\b\\(?:<[0-9]+>\\)?" . font-lock-type-face)
   ;; Array types with sizes: int[5], char[n], int[x + y]
   '("\\b\\(int\\|uint\\|float\\|bool\\|string\\|char\\)\\[[^\\]]+\\]" . font-lock-type-face)
   ;; @from native import directive
   '("@from\\s*(" . font-lock-preprocessor-face)
   ;; Multi-type syntax: [int, float]
   '("\\[[ \t]*\\(int\\|uint\\|float\\|bool\\|string\\|char\\|void\\)[ \t]*\\(,[ \t]*\\(int\\|uint\\|float\\|bool\\|string\\|char\\|void\\)[ \t]*\\)*\\]" . font-lock-type-face)
   ;; Function pointer types: fnc(int, int) : int
   '("fnc[ \t]*([^) ]*)" . font-lock-type-face)
   '("\\b\\(true\\|false\\|null\\|nil\\)\\b" . font-lock-constant-face)
   '("\\b\\_FILEPATH\\_\\|\\_FILENAME\\_\\|\\_PLATFORM\\_\\b" . font-lock-constant-face)
    '("\\b\\(show\\|showb\\|get\\|set\\|toint\\|tofloat\\|tostring\\|cstr\\|lstr\\|size\\|cur\\|name\\|pushb\\|popb\\|pushf\\|popf\\|insert\\|clear\\|remove\\|extend\\|extendv\\|isin\\|rand\\|randf\\|seed\\|choose\\|wait\\|timepass\\|exit\\|exec\\|File\\|open\\|close\\|read\\|write\\|readln\\|readb\\|writeb\\|readlnb\\|replaceall\\|rewind\\|rename\\|delete\\)\\b" . font-lock-builtin-face)
   '("\\b\\(replace\\)\\b" . font-lock-builtin-face)
     '("|>\\|&&\\|||\\|<<\\|>>\\|<>\\|[+\\-*/%&|^~!<>=]=?" . font-lock-variable-name-face)
    '("\\?" . font-lock-variable-name-face)
   '("\\(&\\|\\*\\|->\\)" . font-lock-type-face)
    '("::" . font-lock-special-form-face)
    ;; Hexadecimal: 0xFF, 0xDEADBEEF, 0x1.5p3
    '("\\b0[xX][0-9a-fA-F]+\\(\\.[0-9a-fA-F]*\\)?\\([pP][+-]?[0-9]+\\)?" . font-lock-constant-face)
    ;; Binary: 0b1010
    '("\\b0[bB][01]+" . font-lock-constant-face)
    ;; Octal: 0o755
    '("\\b0[oO][0-7]+" . font-lock-constant-face)
    ;; Scientific notation: 1e10, 2.5E-3, .5
    '("\\b[0-9]+\\(\\.[0-9]*\\)?\\([eE][+-]?[0-9]+\\)+" . font-lock-constant-face)
    '("\\b\\.[0-9]+\\([eE][+-]?[0-9]+\\)+" . font-lock-constant-face)
    ;; Decimal float: 3.14
    '("\\b[0-9]+\\.[0-9]*\\b" . font-lock-constant-face)
    ;; Decimal integer: 42
    '("\\b[0-9]+\\b" . font-lock-constant-face)
   '("\\bclass\\s*(\\s*\\([A-Z][a-zA-Z0-9_]*\\)\\s*)" . font-lock-type-face)
   ;; Type annotation with parent: : Type(Parent)
   '(":\\s*\\([A-Z][a-zA-Z0-9_]*\\)\\s*(\\s*\\([A-Z][a-zA-Z0-9_]*\\)\\s*)" . font-lock-type-face)
   '("\\(?:<\\|,\\)[ \t]*\\([A-Z][a-zA-Z0-9_]*\\)" . (1 font-lock-type-face))
   ;; Function declarations
   '("\\bfnc\\s-+\\([a-zA-Z_][a-zA-Z0-9_]*\\)" 1 font-lock-function-name-face)
   ;; Method calls
   '("\\.\\([a-zA-Z_][a-zA-Z0-9_]*\\)\\s-*(" 1 font-lock-function-name-face)
   )
  "Default font-lock keywords for Leash mode")

(defvar leash-syntax-table
  (let ((st (make-syntax-table)))
    ;; Comments
    ;; /*...*/ - multi-line comments
    (modify-syntax-entry ?/ ". 14" st)
    (modify-syntax-entry ?* ". 23" st)
    (modify-syntax-entry ?\n ">" st)
    ;; Strings: "..." and """..."""
    (modify-syntax-entry ?\" "\"" st)
    (modify-syntax-entry ?' "\"" st)
    (modify-syntax-entry ?\\ "\\" st)
    st)
  "Syntax table for leash-mode")

;;;###autoload
(define-derived-mode leash-mode prog-mode "Leash"
  "Major mode for editing Leash source files."
  :syntax-table leash-syntax-table
  (setq font-lock-defaults '((leash-font-lock-keywords))))

(provide 'leash-mode)

;;; leash-mode.el ends here
