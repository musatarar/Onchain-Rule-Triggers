"""Decoding stored transactions: the token transfer a transaction's calldata makes.

A transaction calling ``transfer`` or ``transferFrom`` on a contract moves that
contract's token, so its ``to``, ``from`` and calldata say which token moved,
between whom and how much. Nothing here checks that the call succeeded or that
the contract is a token: every transfer is stored unverified.
"""

from collections import Counter

from django.db import transaction as db_transaction

from project.app.evm import services
from project.app.evm.block.models import DecodeStatus, Transaction
from project.app.evm.token_transfers import TokenTransfer

BATCH_SIZE = 500

# Each call that moves a token, by its text signature, as the inputs a transfer
# reads: (from, to, value). A ``from`` of None is the transaction's sender.
_TRANSFER_CALLS = {
    "transfer(address,uint256)": (None, 0, 1),
    "transferFrom(address,address,uint256)": (0, 1, 2),
}

# "0x" and the four-byte selector; each input after it is one 32-byte word.
_SELECTOR_LENGTH = 10
_WORD_LENGTH = 64


def decode_transactions(batch_size=BATCH_SIZE):
    """Decode every ``INGESTED`` transaction into the transfer its calldata makes.

    Transactions are claimed ``batch_size`` at a time by moving them to
    ``PROCESSING``, so two runs never decode one transaction twice. One that
    makes a transfer is stored with it and marked ``DECODED``; any other is
    ``UNABLE_TO_DECODE``. Answers how many ended in each status.
    """
    calls = _transfer_calls()
    counts = Counter()
    while batch := _claim(batch_size):
        counts.update(_store(batch, {tx.hash: _transfer(tx, calls) for tx in batch}))
    return counts


def _transfer_calls():
    """The selector of each transfer call the signature catalog holds, as ``(input count, reads)``."""
    return {
        row.hex_signature.lower(): (len(row.input_types()), _TRANSFER_CALLS[text])
        for text, row in services.signatures_for_texts(_TRANSFER_CALLS).items()
    }


def _claim(batch_size):
    """Move up to ``batch_size`` ``INGESTED`` transactions to ``PROCESSING``; answer them.

    Rows another run has locked are skipped rather than waited on, so each
    transaction is claimed by exactly one run.
    """
    with db_transaction.atomic():
        hashes = list(
            Transaction.objects.select_for_update(skip_locked=True)
            .filter(decode_status=DecodeStatus.INGESTED)
            .order_by("chain", "block_number", "transaction_index")
            .values_list("hash", flat=True)[:batch_size]
        )
        Transaction.objects.filter(hash__in=hashes).update(decode_status=DecodeStatus.PROCESSING)
    return list(Transaction.objects.filter(hash__in=hashes))


def _transfer(tx, calls):
    """``(from_address, to_address, raw_value)`` ``tx`` transfers; None when it makes no transfer."""
    call = calls.get(tx.input[:_SELECTOR_LENGTH].lower())
    if tx.to_address is None or call is None:
        return None
    count, (from_input, to_input, value_input) = call
    words = _words(tx.input[_SELECTOR_LENGTH:], count)
    if words is None:
        return None
    from_address = tx.from_address.lower() if from_input is None else _address(words[from_input])
    to_address = _address(words[to_input])
    if from_address is None or to_address is None:
        return None
    return from_address, to_address, words[value_input]


def _words(arguments, count):
    """The first ``count`` 32-byte words of hex ``arguments`` as ints; None when it holds fewer."""
    if len(arguments) < count * _WORD_LENGTH:
        return None
    return [
        int(arguments[index * _WORD_LENGTH : (index + 1) * _WORD_LENGTH], 16)
        for index in range(count)
    ]


def _address(word):
    """An address word as ``0x`` hex; None when bits above its 20 bytes are set."""
    if word >> 160:
        return None
    return f"0x{word:040x}"


def _store(batch, transfers):
    """Store the transfer each of ``batch`` makes and its status; answer the count per status."""
    decoded = {tx.hash: tx for tx in batch if transfers[tx.hash] is not None}
    with db_transaction.atomic():
        tokens = services.tokens_at({(tx.chain, tx.to_address) for tx in decoded.values()})
        rows = []
        for tx in decoded.values():
            from_address, to_address, raw_value = transfers[tx.hash]
            rows.append(
                TokenTransfer(
                    transaction_hash=tx.hash,
                    token=tokens[(tx.chain, tx.to_address.lower())],
                    from_address=from_address,
                    to_address=to_address,
                    raw_value=raw_value,
                )
            )
        TokenTransfer.objects.bulk_create(rows)
        Transaction.objects.filter(hash__in=list(decoded)).update(
            decode_status=DecodeStatus.DECODED
        )
        Transaction.objects.filter(
            hash__in=[tx.hash for tx in batch if tx.hash not in decoded]
        ).update(decode_status=DecodeStatus.UNABLE_TO_DECODE)
    return {
        DecodeStatus.DECODED: len(decoded),
        DecodeStatus.UNABLE_TO_DECODE: len(batch) - len(decoded),
    }
