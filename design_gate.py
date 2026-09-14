"""DEPRECATED shim (#1020) -- the design gate's canonical home is now the
``gates.design`` package (markers / classifiers / gitctx submodules). This module
is kept for ONE release so the existing importers -- ``import design_gate as dg``
across tests + hooks, ``scripts/measure_design_compliance.py`` and
``scripts/replay_design_gate_commit_corpus.py`` -- keep working unchanged.

New code should import from ``gates.design`` directly. Every attribute access on
this module delegates to the package via PEP 562 module ``__getattr__`` -- so
``design_gate.classify_design_comment``, ``design_gate._strip_quoted``,
``design_gate.ISSUE_REF_RE`` and ``design_gate.subprocess`` (patched globally by a
test) all resolve to the real objects in the package, which is where the code
lives now.
"""
import gates.design as _design


def __getattr__(name):
    """Delegate any lookup this shim does not define to gates.design (PEP 562).
    Covers public API, single-underscore private names, and the ``subprocess``
    module object a test patches globally."""
    try:
        return getattr(_design, name)
    except AttributeError:
        raise AttributeError(
            "module 'design_gate' (deprecated shim, see gates.design) has no "
            "attribute %r" % name) from None
