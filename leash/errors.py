class LeashError(Exception):
    def __init__(self, msg, line=None, col=None, tip=None):
        self.msg = msg
        self.line = line
        self.col = col
        self.tip = tip
        super().__init__(msg)
