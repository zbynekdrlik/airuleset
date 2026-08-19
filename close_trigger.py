"""Stub for #567 — worker close-trigger detection not yet implemented (RED).

The real grammar/extraction/context logic lands in the GREEN commit; these
no-op stubs exist only so the RED test can invoke the module + hook and FAIL
on behaviour (never an ImportError).
"""


def find_close_trigger(text):
    return None


def is_worker_context(cwd, agent_type):
    return False


def commit_message_texts(cmd, cwd):
    return []


def scan_commit_command(cmd, cwd):
    return None
