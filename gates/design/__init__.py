"""gates.design -- the design/validated/reviewed/lane-return gate package
(#1020, split out of the old top-level design_gate.py).

Re-exports every name from the three submodules (markers + classifiers + gitctx)
into the package namespace -- including single-underscore private names some
tests/hooks reference (design_gate._strip_quoted, ._gh_issue_state, ._CAUSE_RE)
and the `subprocess` module object a test patches globally. A plain attribute
copy (not `import *`) is used precisely so those private names come across too.
"""
import sys as _sys

from gates.design import classifiers as _classifiers
from gates.design import gitctx as _gitctx
from gates.design import markers as _markers

_self = _sys.modules[__name__]
for _mod in (_markers, _classifiers, _gitctx):
    for _name in dir(_mod):
        if _name.startswith("__"):
            continue
        setattr(_self, _name, getattr(_mod, _name))
