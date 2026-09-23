"""The function catalog: a four-byte selector, the function it decodes to, and that one's inputs."""

import re

from django.db import models
from django.db.models import Q
from pydantic import BaseModel, field_validator

# "transferFrom(address,address,uint256)": the name, then everything it takes.
_SIGNATURE_RE = re.compile(r"^([^(]*)\((.*)\)$", re.DOTALL)

# Where one camelCase word ends and the next begins, including the run of
# capitals in "ERC20Transfer", which ends one character before "Transfer".
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class Signature(BaseModel):
    """One parsed signature: what the function is called and what it takes."""

    name: str
    inputs: list[str]


class FunctionInputSchema(BaseModel):
    """One argument a function takes: its type, and a tuple's own arguments in order."""

    param_type: str
    components: list["FunctionInputSchema"] = []


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


def parse_input(type_text):
    """An argument type as the input it declares, a tuple as ``tuple`` over its components.

    ``(address,uint256)[]`` is a ``tuple[]`` whose components are an ``address``
    and a ``uint256``, the way an ABI writes it. A tuple that never closes is
    no tuple, so its text is kept whole as the type.
    """
    if not type_text.startswith("("):
        return FunctionInputSchema(param_type=type_text)
    depth = 0
    for end, char in enumerate(type_text):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0:
            return FunctionInputSchema(
                param_type="tuple" + type_text[end + 1 :],
                components=[parse_input(component) for component in _inputs(type_text[1:end])],
            )
    return FunctionInputSchema(param_type=type_text)


class SmartContractFunctionCreateSchema(BaseModel):
    """A function that is not stored yet."""

    signature_hash: str
    function_name: str
    full_signature: str
    inputs: list[FunctionInputSchema]

    @field_validator("signature_hash")
    @classmethod
    def _lowercase(cls, signature_hash):
        """One selector is one row however its hex was written."""
        return signature_hash.lower()


class SmartContractFunctionUpdateSchema(BaseModel):
    """What changes on a stored function; its selector names it, so that never does.

    Its inputs are the full signature's, so they are stored anew only when that changes.
    """

    function_name: str
    full_signature: str


class StateMutability(models.TextChoices):
    """What a call to a function may do to chain state, and whether it takes ether."""

    PURE = "pure", "Pure"
    VIEW = "view", "View"
    NONPAYABLE = "nonpayable", "Non-payable"
    PAYABLE = "payable", "Payable"


class SmartContractFunction(models.Model):
    """One function a selector decodes to: its name, its text signature, and its inputs.

    A selector is four bytes of a hash, so several functions can share one; the
    catalog keeps one per ``signature_hash``, and saving another function under
    a stored selector replaces it.
    """

    function_name = models.CharField(max_length=255)  # "swapExactTokensForTokens"
    signature_hash = models.CharField(max_length=10, unique=True)  # "0x38ed1739"
    # "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)"
    full_signature = models.TextField()
    # A text signature does not say; learned later, so in neither schema, and a
    # save never sets or clears it.
    state_mutability = models.CharField(
        max_length=50, choices=StateMutability.choices, null=True, blank=True
    )

    class Meta:
        db_table = "smart_contract_functions"
        ordering = ["-id"]

    def pretty_signature(self):
        """The name's camelCase and snake_case runs read as words, the inputs as written."""
        return Signature(
            name=_worded(self.function_name), inputs=parse_signature(self.full_signature).inputs
        )

    def __str__(self):
        return f"{self.signature_hash} {self.full_signature}"


class FunctionInput(models.Model):
    """One argument a function takes, at its position; a tuple's components sit under it.

    A top-level input has no ``parent_input``. A component names the tuple it
    belongs to, and its ``position_index`` counts within that tuple.
    """

    function = models.ForeignKey(
        SmartContractFunction, on_delete=models.CASCADE, related_name="inputs"
    )
    # A text signature names no arguments; learned later, like state_mutability.
    param_name = models.CharField(max_length=255, null=True, blank=True)  # "amountIn"
    param_type = models.CharField(max_length=100)  # "uint256", "address[]", "tuple"
    position_index = models.IntegerField()  # 0 for the first argument
    parent_input = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="components"
    )

    class Meta:
        db_table = "function_inputs"
        constraints = [
            models.UniqueConstraint(
                fields=["function", "position_index", "parent_input"],
                name="function_input_position_unique",
            ),
            # Two NULLs never collide under a unique constraint, so the one
            # above leaves top-level inputs, which have no parent, unguarded.
            models.UniqueConstraint(
                fields=["function", "position_index"],
                condition=Q(parent_input__isnull=True),
                name="function_input_top_level_position_unique",
            ),
        ]

    def __str__(self):
        return f"{self.param_type} {self.param_name}" if self.param_name else self.param_type
