"""Concatenate library modules into source text for Freya code nodes."""
import inspect


def source(*modules):
    parts = [inspect.getsource(m) for m in modules]
    src = "\n\n".join(parts)
    assert "{{" not in src and "}}" not in src, "embedded source contains a brace pair"
    return src
