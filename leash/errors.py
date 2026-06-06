class LeashError(Exception):
    def __init__(
        self, msg, line=None, col=None, tip=None, file=None, code=None, severity="error", node=None
    ):
        self.msg = msg
        if node is not None:
            self.line = getattr(node, "line", None)
            self.col = getattr(node, "col", None)
            self.file = getattr(node, "source_file", None)
        else:
            self.line = line
            self.col = col
            self.file = file
        self.tip = tip
        self.code = code
        self.severity = severity
        super().__init__(msg)
