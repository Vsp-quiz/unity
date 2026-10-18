"""Stub: ASTC textures need the real astc-encoder-py wheel (pip install astc-encoder-py)."""
class _S:
    def __getattr__(self, n):
        return _S()
    def __call__(self, *a, **k):
        raise NotImplementedError("ASTC texture support needs `pip install astc-encoder-py`")
    @classmethod
    def from_str(cls, s):
        return cls()
ASTCImage = ASTCType = ASTCSwizzle = ASTCConfig = ASTCConfigFlags = ASTCProfile = ASTCContext = _S()

_IS_SHIM = True
