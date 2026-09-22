"""The signature catalog: a function selector and the text signature it decodes to."""

import re

from django.db import models
from pydantic import BaseModel

# "transferFrom(address,address,uint256)": the name, then everything it takes.
_SIGNATURE_RE = re.compile(r"^([^(]*)\((.*)\)$", re.DOTALL)

# Where one camelCase word ends and the next begins, including the run of
# capitals in "ERC20Transfer", which ends one character before "Transfer".
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class Signature(BaseModel):
    """One parsed signature: what the function is called and what it takes."""

    name: str
    inputs: list[str]


def _inputs(arguments):
    """The top-level argument types; a tuple's own commas sit inside its parentheses."""
    types, depth, current = [], 0, ""
    for char in arguments:
        if char == "," and depth == 0:
            types.append(current)
            current = ""
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        current += char
    types.append(current)
    return [argument.strip() for argument in types if argument.strip()]


def _worded(name):
    """A camelCase or snake_case name as capitalised words, acronyms left as written."""
    words = _CAMEL_BOUNDARY_RE.sub(" ", name).replace("_", " ").split()
    return " ".join(word[:1].upper() + word[1:] for word in words)


class FunctionSignature(models.Model):
    """One catalog entry: a selector and a text signature it decodes to.

    ``id`` is the one the source assigned, so re-loading a page updates the
    rows it first wrote. A selector is four bytes of a hash, so several text
    signatures share one ``hex_signature``: the column is indexed, never unique.
    """

    id = models.BigIntegerField(primary_key=True)
    hex_signature = models.CharField(max_length=10, db_index=True)  # "0xc1c3d3d9"
    text_signature = models.CharField(max_length=512)  # "transferFrom(address,address,uint256)"
    # When the source recorded it, not when this row was loaded.
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-id"]

    def signature(self):
        """The stored text as its name and the input types it declares."""
        match = _SIGNATURE_RE.match(self.text_signature or "")
        if match is None:
            # A row stored without an argument list is all name.
            return Signature(name=(self.text_signature or "").strip(), inputs=[])
        name, arguments = match.groups()
        return Signature(name=name.strip(), inputs=_inputs(arguments))

    def pretty_signature(self):
        """:meth:`signature` with the name's camelCase and snake_case runs read as words."""
        parsed = self.signature()
        return Signature(name=_worded(parsed.name), inputs=parsed.inputs)

    def __str__(self):
        return f"{self.hex_signature} {self.text_signature}"
