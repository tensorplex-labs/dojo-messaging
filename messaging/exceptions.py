class InvalidSignatureException(Exception):
    """Exception raised when signature is invalid"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)
