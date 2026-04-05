class LeashError(Exception):
    def __init__(
        self, msg, line=None, col=None, tip=None, file=None, code=None, severity="error"
    ):
        self.msg = msg
        self.line = line
        self.col = col
        self.tip = tip
        self.file = file
        self.code = code
        self.severity = severity
        super().__init__(msg)
