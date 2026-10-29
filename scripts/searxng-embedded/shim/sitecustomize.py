"""Python 3.10 垫片：把 typing_extensions 中 3.11+ 的 typing 特性补进 typing 模块。"""
import typing as _typing
try:
    import typing_extensions as _te
except ImportError:
    _te = None
if _te:
    for _name in ("NotRequired", "Required", "Self", "TypeVarTuple", "TypeAliasType"):
        if not hasattr(_typing, _name) and hasattr(_te, _name):
            setattr(_typing, _name, getattr(_te, _name))
