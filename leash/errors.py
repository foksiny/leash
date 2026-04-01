class LeashError(Exception):
    def __init__(self, msg, line=None, col=None, tip=None, file=None):
        self.msg = msg
        self.line = line
        self.col = col
        self.tip = tip
        self.file = file  # File path where the error occurred
        super().__init__(msg)
