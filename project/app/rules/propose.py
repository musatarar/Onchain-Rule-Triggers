"""Sentence -> single token-transfer rule, through the LLM's structured output.

The LLM only extracts four strings (:class:`TransferDraft`); turning them into
a ``ConditionNode`` tree, resolving the token symbol against the catalog and
picking a tag and glyph happen here, so the model never invents an address.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from project.app.constants import GLYPH_CHOICES
from project.app.evm import services as evm_services
from project.app.evm.chains import ChainId
from project.app.rules import utils
from project.app.rules.models import Rule
from project.app.services.llm import get_llm_client

PROMPT = """Extract a token transfer rule from the user's sentence.
Return null for any field the sentence does not mention.
- token: the token's ticker symbol, uppercase (e.g. "USDC"; "Ether.fi" ->
  "ETHFI"). If unsure of the ticker, give the name as written.
- amount: the amount as a plain decimal number in whole tokens, no commas or
  units ("over 10" -> "10", "1.5k" -> "1500").
- amount_operator: how the transfer compares to amount: "gt" (over, more
  than), "gte" (at least), "lt" (under, less than), "lte" (at most), "eq"
  (exactly). Null when no amount is mentioned.
- from_address: the sender 0x address, exactly as written.
- to_address: the recipient 0x address, exactly as written.

Sentence: {sentence}"""

logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
# "10000", "10,000", "10k", "$1.5m": the number, then an optional magnitude.
AMOUNT_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(k|m|mm|b|bn)?", re.IGNORECASE)
MAGNITUDES = {"k": 3, "m": 6, "mm": 6, "b": 9, "bn": 9}


AmountOperator = Literal["gt", "gte", "lt", "lte", "eq"]
OPERATOR_SYMBOLS = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "="}


class TransferDraft(BaseModel):
    """The answers the LLM gives for one sentence; null when not mentioned."""

    token: str | None = Field(description="Token symbol, e.g. USDC")
    amount: str | None = Field(description="Amount in whole tokens, e.g. 10")
    amount_operator: AmountOperator | None = Field(
        description="gt, gte, lt, lte or eq; null means gt"
    )
    from_address: str | None = Field(description="Sender 0x address")
    to_address: str | None = Field(description="Recipient 0x address")


def extract(sentence):
    """One structured LLM call: the sentence's :class:`TransferDraft`."""
    client = get_llm_client()
    return client.generate_structured(PROMPT.format(sentence=sentence), TransferDraft).parsed


def propose(owner, sentence, chain=ChainId.ETHEREUM):
    """The ``Proposal`` for ``sentence``, or ``ValidationError`` when nothing usable came back."""
    draft = extract(sentence)
    children, understood = [], []

    def compare(field, operator, value):
        children.append(
            {
                "id": None,
                "type": "comparison",
                "source": utils.SOURCE_TOKEN_TRANSFER,
                "field": field,
                "operator": operator,
                "value": value,
            }
        )

    token = _token(draft.token, chain)
    if token is not None:
        compare("token", "eq", {"chain": chain, "address": token.contract.address})
        understood.append(token.symbol)
    amount = _amount(draft.amount)
    if amount is not None:
        # A sentence with an amount but no comparison word ("10 USDC") reads as "over".
        operator = draft.amount_operator or "gt"
        compare("amount", operator, amount)
        understood.append(f"{OPERATOR_SYMBOLS[operator]} {amount}")
    for field, label in (("from_address", "from"), ("to_address", "to")):
        address = getattr(draft, field)
        if address and ADDRESS_RE.fullmatch(address.strip()):
            compare(field, "eq", address.strip().lower())
            understood.append(f"{label} {address.strip()[:6]}…{address.strip()[-4:]}")

    if not children:
        logger.info("propose: nothing usable in %r; the LLM returned %r", sentence, draft)
        raise ValidationError("Couldn't turn that into conditions.", code="not_understood")
    condition = {"id": None, "type": "and", "children": children}
    utils.validate_condition(condition)
    return {
        "condition": condition,
        "name": sentence.strip()[:80] or "Token transfer",
        "tag": _free_tag(owner, token.symbol if token else "XFER"),
        "glyph": _free_glyph(owner),
        "understood": understood,
    }


def _token(text, chain):
    """The catalog token on ``chain`` whose symbol, or else whose name, is exactly
    ``text`` (case ignored), or None. "ETHFI" and "Ether.fi" both find ETHFI."""
    if not text:
        return None
    text = text.strip().upper()
    candidates = list(evm_services.search_tokens(text, chain)[:20])
    for attribute in ("symbol", "name"):
        for token in candidates:
            if (getattr(token, attribute) or "").upper() == text:
                return token
    return None


def _amount(text):
    """``text`` as a plain decimal string ("10k" -> "10000", "1,500" -> "1500"), or None."""
    match = AMOUNT_RE.fullmatch((text or "").strip())
    if match is None:
        return None
    try:
        number = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    number = number.scaleb(MAGNITUDES.get((match.group(2) or "").lower(), 0))
    return utils.decimal_string(number)


def _free_tag(owner, base):
    """``base`` (or ``base-2``, ``base-3``…) cut to fit the tag form and unused by ``owner``."""
    base = re.sub(r"[^A-Z0-9-]", "", base.upper())[:9] or "XFER"
    taken = set(Rule.objects.filter(owner=owner).values_list("tag", flat=True))
    tag, n = base, 2
    while tag in taken:
        tag, n = f"{base}-{n}", n + 1
    return tag


def _free_glyph(owner):
    """The first glyph ``owner`` hasn't used, or the first glyph when all are."""
    used = set(Rule.objects.filter(owner=owner).values_list("glyph", flat=True))
    return next((key for key, _ in GLYPH_CHOICES if key not in used), GLYPH_CHOICES[0][0])
