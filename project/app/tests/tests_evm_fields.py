"""``AddressField``: whatever writes an address, it is stored lowercased, and an
exact or ``in`` lookup finds it whatever case it is looked up in."""

from django.test import TestCase

from project.app.evm.block import services as block_services
from project.app.evm.chains import ChainId
from project.app.models import Block, Contract, Token, TokenTransfer, Transaction
from project.app.tests.tests_evm_block import LEGACY_HASH, block, legacy_transaction

CHECKSUMMED = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
LOWER = CHECKSUMMED.lower()


class AddressFieldTests(TestCase):
    def test_a_save_stores_the_address_lowercased_and_the_instance_holds_it(self):
        # A transfer has no schema in front of it: only the column folds its addresses.
        contract = Contract.objects.create(chain=ChainId.ETHEREUM, address=CHECKSUMMED)
        transfer = TokenTransfer.objects.create(
            transaction_hash="0x" + "ab" * 32,
            token=Token.objects.create(contract=contract),
            from_address=CHECKSUMMED,
            to_address=CHECKSUMMED,
            raw_value=1,
        )

        self.assertEqual((contract.address, transfer.from_address), (LOWER, LOWER))
        self.assertEqual(Contract.objects.values_list("address", flat=True).get(), LOWER)
        self.assertEqual(
            TokenTransfer.objects.values_list("from_address", "to_address").get(), (LOWER, LOWER)
        )

    def test_an_update_to_a_literal_is_stored_lowercased(self):
        block_services.store_blocks([block()], ChainId.ETHEREUM)

        Block.objects.update(miner=CHECKSUMMED)

        self.assertEqual(Block.objects.values_list("miner", flat=True).get(), LOWER)

    def test_storing_a_block_again_folds_the_addresses_its_upsert_writes(self):
        block_services.store_blocks([block()], ChainId.ETHEREUM)

        block_services.store_blocks(
            [block(transactions=[legacy_transaction(**{"from": CHECKSUMMED})])],
            ChainId.ETHEREUM,
        )

        self.assertEqual(Transaction.objects.get(hash=LEGACY_HASH).from_address, LOWER)

    def test_an_exact_or_in_lookup_finds_the_address_whatever_its_case(self):
        contract = Contract.objects.create(chain=ChainId.ETHEREUM, address=LOWER)

        self.assertEqual(Contract.objects.get(address=CHECKSUMMED), contract)
        self.assertEqual(list(Contract.objects.filter(address__in=[CHECKSUMMED])), [contract])
