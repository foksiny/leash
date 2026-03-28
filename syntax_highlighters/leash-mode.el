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
   '("\\b\\(fnc\\|def\\|struct\\|union\\|enum\\|type\\|return\\|if\\|also\\|else\\|while\\|for\\|do\\|foreach\\|in\\)\\b" . font-lock-keyword-face)
   '("\\b\\(int\\|uint\\|float\\|bool\\|string\\|char\\|void\\)\\b\\(?:<[0-9]+>\\)?" . font-lock-type-face)
   '("\\b\\(true\\|false\\)\\b" . font-lock-constant-face)
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
