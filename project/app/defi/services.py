"""Reading the signature catalog: what one four-byte selector might decode to."""

from project.app.defi.function_signatures import FunctionSignature


def signatures_for_selector(hex_signature):
    """Every stored signature a selector decodes to, earliest directory entry first.

    A selector is a truncated hash, so this answers with candidates: which one
    the calldata actually supports is the caller's to decide.
    """
    return list(
        FunctionSignature.objects.filter(hex_signature=(hex_signature or "").lower()).order_by("id")
    )
