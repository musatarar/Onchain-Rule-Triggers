"""The signature catalog: a function selector and the text signature it decodes to."""

import re

from django.db import models
from pydantic import BaseModel, model_validator

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


def parse_signature(text):
    """A text signature as its name and the input types it declares."""
    match = _SIGNATURE_RE.match(text or "")
    if match is None:
        # Text without an argument list is all name.
        return Signature(name=(text or "").strip(), inputs=[])
    name, arguments = match.groups()
    return Signature(name=name.strip(), inputs=_inputs(arguments))


class FunctionSignatureCreateSchema(BaseModel):
    """A catalog entry that is not stored yet, its ``inputs`` the types it takes in order.

    ``input_names`` names those inputs position by position; None when the
    source gave only the types, as a text signature does.
    """

    id: int
    hex_signature: str
    name: str
    inputs: list[str]
    input_names: list[str] | None = None

    @model_validator(mode="after")
    def _one_name_per_input(self):
        if self.input_names is not None and len(self.input_names) != len(self.inputs):
            raise ValueError(
                f"{len(self.input_names)} input name(s) given for {len(self.inputs)} input(s)."
            )
        return self


class FunctionSignatureUpdateSchema(BaseModel):
    """What changes on a stored entry's own row; its id names it, so that never does.

    Its inputs are rows of their own, so a save replaces them apart from these.
    """

    hex_signature: str
    name: str


class InputsNotFetched(RuntimeError):
    """A signature's inputs were read without ``with_inputs()`` having fetched them."""


class FunctionSignatureQuerySet(models.QuerySet):
    def with_inputs(self):
        """Fetch each signature's inputs alongside it, for reading ``input_types()``."""
        return self.prefetch_related("inputs")


class FunctionSignature(models.Model):
    """One catalog entry: a selector and a function it decodes to.

    ``id`` is the one the source assigned, so saving an entry again updates the
    row it first wrote. A selector is four bytes of a hash, so several
    functions share one ``hex_signature``: the column is indexed, never unique.
    """

    id = models.BigIntegerField(primary_key=True)
    hex_signature = models.CharField(max_length=10, db_index=True)  # "0xc1c3d3d9"
    name = models.CharField(max_length=255)  # "transferFrom"
    # What a reader adds: in neither schema, so a save never sets or clears it.
    description = models.TextField(blank=True, default="")

    objects = FunctionSignatureQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]

    def _fetched_inputs(self):
        """The inputs ``with_inputs()`` fetched, in order.

        Raises ``InputsNotFetched`` rather than query here, where a loop over
        signatures would run one query per row without saying so.
        """
        cache = getattr(self, "_prefetched_objects_cache", {})
        name = self._meta.get_field("inputs").get_cache_name()
        if name not in cache:
            raise InputsNotFetched(
                f"Signature {self.pk} was fetched without its inputs; "
                "fetch it with FunctionSignature.objects.with_inputs()."
            )
        return list(cache[name])

    @property
    def is_decoded(self):
        """Whether every input has a name; one taking nothing has none left to name."""
        return all(function_input.name for function_input in self._fetched_inputs())

    def input_types(self):
        """The types it takes, in order: ["address", "address", "uint256"]."""
        return [function_input.type for function_input in self._fetched_inputs()]

    def input_names(self):
        """The names of what it takes, in order; None where one is not known."""
        return [function_input.name for function_input in self._fetched_inputs()]

    def pretty_signature(self):
        """The name's camelCase and snake_case runs read as words, the inputs as stored."""
        return Signature(name=_worded(self.name), inputs=self.input_types())

    def __str__(self):
        # Printed in tracebacks and test failures too, so it never raises or
        # queries: inputs not fetched read as an ellipsis.
        try:
            inputs = ",".join(self.input_types())
        except InputsNotFetched:
            inputs = "..."
        return f"{self.hex_signature} {self.name}({inputs})"


class FunctionInput(models.Model):
    """One parameter of a signature: where it sits, what type it is, and its name if known."""

    function_signature = models.ForeignKey(
        FunctionSignature, on_delete=models.CASCADE, related_name="inputs"
    )
    index = models.PositiveSmallIntegerField()  # 0 for the first parameter
    # "from", "to", "value"; None when the source gave only the type, as a text signature does.
    name = models.CharField(max_length=255, blank=True, null=True, default=None)
    type = models.CharField(max_length=255)  # "address", "uint256"

    class Meta:
        ordering = ["index"]
        constraints = [
            # One parameter per position, so the order reads back as it was saved.
            models.UniqueConstraint(
                fields=["function_signature", "index"], name="function_input_index_unique"
            ),
        ]

    def __str__(self):
        return f"{self.index}: {self.type} {self.name or ''}".rstrip()
