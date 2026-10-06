"""Decoding stored transactions: the token transfers each one makes.

A transaction stored with its receipt makes the transfers its logs record: one
per ``Transfer`` event, whichever contract emitted it, so a token a contract it
calls moves on its behalf (a swap, a multisig execution) is one of them. A
receipt whose ``status`` says the transaction reverted records none, and a
removed log, one a reorg dropped, is no transfer. Each keeps its log's index,
and is ``verified`` when a token the catalog recognises emitted it; an event
from an unknown contract is stored unverified, since any contract can emit one.

A transaction stored without its receipt falls back to its calldata: calling
``transfer`` or ``transferFrom`` on a contract moves that contract's token, so
its ``to``, ``from`` and calldata say which token moved, between whom and how
much. Nothing checks that such a call succeeded or that the contract is a
token, so that transfer is stored unverified and with no log index.
"""

from collections import Counter

from django.db import transaction as db_transaction

from project.app.evm import services
from project.app.evm.block.models import DecodeStatus, Transaction
from project.app.evm.receipt.models import Receipt
from project.app.evm.token_transfers import TokenTransfer

BATCH_SIZE = 500

# Each call that moves a token, by its text signature, as the inputs a transfer
# reads: (from, to, value). A ``from`` of None is the transaction's sender.
_TRANSFER_CALLS = {
    "transfer(address,uint256)": (None, 0, 1),
    "transferFrom(address,address,uint256)": (0, 1, 2),
}

# Topic 0 of a Transfer(address,address,uint256) event, its keccak-256 hash.
# ERC-20 and ERC-721 share it: ERC-20 indexes the two addresses and puts the
# amount in the data, ERC-721 indexes the token id as a third topic.
TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_ERC20_TOPICS = 3
_ERC721_TOPICS = 4

# "0x" and the four-byte selector; each input after it is one 32-byte word.
_SELECTOR_LENGTH = 10
_WORD_LENGTH = 64


def decode_transactions(batch_size=BATCH_SIZE):
    """Decode every ``INGESTED`` transaction into the transfers it makes.

    Transactions are claimed ``batch_size`` at a time by moving them to
    ``PROCESSING``, so two runs never decode one transaction twice. One that
    makes a transfer is stored with its transfers and marked ``DECODED``; any
    other is ``UNABLE_TO_DECODE``. Answers how many ended in each status.
    """
    calls = _transfer_calls()
    counts = Counter()
    while batch := _claim(batch_size):
        receipts = _receipts(batch)
        counts.update(
            _store(batch, {tx.hash: _transfers(tx, calls, receipts.get(tx.hash)) for tx in batch})
        )
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


def _receipts(batch):
    """The receipt of each transaction in ``batch`` that has one stored, by hash, logs and topics prefetched.

    A receipt is keyed by transaction hash alone, and a transaction replayed on
    another chain keeps its hash, so a receipt is a transaction's only when it
    is on the transaction's chain and, when the transaction names its block,
    in that block: a receipt from before a reorg recorded another execution.
    """
    by_hash = {tx.hash: tx for tx in batch}
    receipts = Receipt.objects.filter(transaction_hash__in=list(by_hash)).prefetch_related(
        "logs__topics"
    )
    return {
        receipt.transaction_hash: receipt
        for receipt in receipts
        if receipt.chain == by_hash[receipt.transaction_hash].chain
        and by_hash[receipt.transaction_hash].block_hash in (None, receipt.block_hash)
    }


def _transfers(tx, calls, receipt):
    """Every transfer ``tx`` makes, as ``(contract, log_index, from_address, to_address, raw_value)``.

    With its ``receipt``, one per ``Transfer`` event its logs carry, in log
    order, and none when it reverted. Without, the transfer its calldata makes,
    with a log index of None, or none.
    """
    if receipt is None:
        transfer = _call_transfer(tx, calls)
        return [] if transfer is None else [(tx.to_address, None, *transfer)]
    if receipt.status != 1:  # reverted: whatever it emitted was rolled back with it
        return []
    return [transfer for log in receipt.logs.all() if (transfer := _log_transfer(log)) is not None]


def _log_transfer(log):
    """``(contract, log_index, from_address, to_address, raw_value)`` of a Transfer ``log``; None for any other log."""
    topics = [topic.data.lower() for topic in log.topics.all()]
    if log.removed or not topics or topics[0] != TRANSFER_EVENT_TOPIC:
        return None
    if len(topics) == _ERC20_TOPICS:
        words = _words(log.data[2:], 1)
        if words is None:
            return None
        raw_value = words[0]
    elif len(topics) == _ERC721_TOPICS:
        raw_value = int(topics[3], 16)
    else:
        return None
    from_address, to_address = _address(int(topics[1], 16)), _address(int(topics[2], 16))
    if from_address is None or to_address is None:
        return None
    return log.address, log.log_index, from_address, to_address, raw_value


def _call_transfer(tx, calls):
    """``(from_address, to_address, raw_value)`` ``tx``'s calldata transfers; None when it makes no transfer."""
    call = calls.get(tx.input[:_SELECTOR_LENGTH].lower())
    if tx.to_address is None or call is None:
        return None
    count, (from_input, to_input, value_input) = call
    words = _words(tx.input[_SELECTOR_LENGTH:], count)
    if words is None:
        return None
    from_address = tx.from_address if from_input is None else _address(words[from_input])
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
    """Store the transfers each of ``batch`` makes and its status; answer the count per status."""
    decoded = [tx for tx in batch if transfers[tx.hash]]
    with db_transaction.atomic():
        tokens = services.tokens_at(
            {(tx.chain, contract) for tx in decoded for contract, *_ in transfers[tx.hash]}
        )
        rows = []
        for tx in decoded:
            for contract, log_index, from_address, to_address, raw_value in transfers[tx.hash]:
                token = tokens[(tx.chain, contract.lower())]
                rows.append(
                    TokenTransfer(
                        transaction_hash=tx.hash,
                        chain=tx.chain,
                        block_number=tx.block_number,
                        block_hash=tx.block_hash,
                        block_timestamp=tx.block_timestamp,
                        transaction_index=tx.transaction_index,
                        log_index=log_index,
                        token=token,
                        from_address=from_address,
                        to_address=to_address,
                        raw_value=raw_value,
                        # A placeholder token has no name: the catalog does not recognise it.
                        verified=log_index is not None and token.name is not None,
                    )
                )
        TokenTransfer.objects.bulk_create(rows)
        Transaction.objects.filter(hash__in=[tx.hash for tx in decoded]).update(
            decode_status=DecodeStatus.DECODED
        )
        Transaction.objects.filter(
            hash__in=[tx.hash for tx in batch if not transfers[tx.hash]]
        ).update(decode_status=DecodeStatus.UNABLE_TO_DECODE)
    return {
        DecodeStatus.DECODED: len(decoded),
        DecodeStatus.UNABLE_TO_DECODE: len(batch) - len(decoded),
    }
