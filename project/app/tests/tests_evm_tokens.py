"""The token catalog: how tokens are saved, and which of a file's coin platforms become tokens."""

import datetime
import io
import json
import os
import tempfile

from django.core.management import CommandError, call_command
from django.test import TestCase
from pydantic import ValidationError

from project.app.evm import services
from project.app.evm.chains import ChainId
from project.app.evm.tokens import TokenCreateSchema
from project.app.models import Contract, FunctionSignature, Token

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
# Sei lists some tokens by their Cosmos address, which is no EVM address.
SEI_COSMOS = "sei1hrndqntlvtmx2kepr0zsfgr7nzjptcc72cr4ppk4yav58vvy7v3s4er8ed"


def coin(coingecko_id, name=None, **platforms):
    """A tokens.json entry; keyword ``binance_smart_chain`` stands for ``binance-smart-chain``."""
    return {
        "rank": 1,
        "id": coingecko_id,
        "symbol": "SYM",
        "name": coingecko_id.title() if name is None else name,
        "market_cap_usd": 1,
        "ethereum_address": platforms.get("ethereum", ""),
        "all_platforms": {slug.replace("_", "-"): address for slug, address in platforms.items()},
    }


def address(n):
    return f"0x{n:040x}"


def token(name="Tether", coingecko_id="tether", chain=ChainId.ETHEREUM, at=USDT):
    return TokenCreateSchema(name=name, coingecko_id=coingecko_id, chain=chain, address=at)


def load(entries, **options):
    """Run the command against ``entries`` written to a raw_data-style file; answer its output."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "tokens.json")
        with open(path, "w", encoding="utf-8") as raw:
            json.dump(entries, raw)
        out = io.StringIO()
        call_command("load_tokens", path=path, stdout=out, **options)
    return out.getvalue()


class TokenCreateSchemaTests(TestCase):
    def test_an_address_is_lowercased(self):
        self.assertEqual(token(at="0x" + USDT[2:].upper()).address, USDT)

    def test_a_chain_is_read_as_its_chain_id(self):
        self.assertIs(token(chain=8453).chain, ChainId.BASE)

    def test_a_chain_without_an_id_is_refused(self):
        with self.assertRaises(ValidationError):
            token(chain=424242)


class SaveTokenTests(TestCase):
    def test_stores_a_new_token(self):
        row = services.save_token(token())

        self.assertEqual(Token.objects.get(), row)
        self.assertEqual((row.name, row.coingecko_id), ("Tether", "tether"))
        self.assertEqual((row.chain, row.address), (ChainId.ETHEREUM, USDT))

    def test_saving_a_stored_chain_and_address_updates_its_row(self):
        services.save_token(token(name="Tether"))

        row = services.save_token(token(name="Tether USD"))

        self.assertEqual(Token.objects.count(), 1)
        self.assertEqual(row.name, "Tether USD")

    def test_the_same_address_on_another_chain_is_another_row(self):
        services.save_token(token(chain=ChainId.ETHEREUM))
        services.save_token(token(chain=ChainId.BASE))

        self.assertEqual(Token.objects.count(), 2)

    def test_an_address_is_stored_lowercase(self):
        services.save_token(token(at="0x" + USDT[2:].upper()))
        services.save_token(token(at=USDT))

        self.assertEqual(list(Token.objects.values_list("address", flat=True)), [USDT])

    def test_saving_again_leaves_verification_and_functions_alone(self):
        row = services.save_token(token())
        transfer = FunctionSignature.objects.create(
            id=1, hex_signature="0xa9059cbb", name="transfer"
        )
        row.contract_is_verified = True
        row.save()
        row.functions.add(transfer)

        row = services.save_token(token(name="Tether USD"))

        self.assertTrue(row.contract_is_verified)
        self.assertEqual(list(row.functions.all()), [transfer])
        self.assertEqual(list(transfer.tokens.all()), [row])

    def test_a_token_is_a_contract_sharing_its_id(self):
        row = services.save_token(token())

        contract = Contract.objects.get()
        self.assertEqual(contract.pk, row.pk)
        self.assertEqual((contract.chain, contract.address), (ChainId.ETHEREUM, USDT))

    def test_a_stored_contract_becomes_the_token_keeping_its_creation_date(self):
        created = datetime.datetime(2017, 11, 28, tzinfo=datetime.UTC)
        contract = Contract.objects.create(
            chain=ChainId.ETHEREUM, address=USDT, creation_date=created
        )

        row = services.save_token(token())

        self.assertEqual(Contract.objects.count(), 1)
        self.assertEqual(row.pk, contract.pk)
        self.assertEqual((row.name, row.creation_date), ("Tether", created))


class TokensAtTests(TestCase):
    def test_an_unknown_contract_gets_a_placeholder_token(self):
        tokens = services.tokens_at({(ChainId.ETHEREUM, "0x" + USDT[2:].upper())})

        row = tokens[(ChainId.ETHEREUM, USDT)]
        self.assertEqual((row.name, row.coingecko_id), (None, None))
        self.assertEqual(Contract.objects.get().pk, row.pk)

    def test_a_stored_token_is_answered_not_duplicated(self):
        stored = services.save_token(token())

        tokens = services.tokens_at({(ChainId.ETHEREUM, USDT)})

        self.assertEqual(tokens, {(ChainId.ETHEREUM, USDT): stored})
        self.assertEqual(Contract.objects.count(), 1)

    def test_a_stored_contract_that_is_no_token_yet_becomes_one(self):
        contract = Contract.objects.create(chain=ChainId.ETHEREUM, address=USDT)

        tokens = services.tokens_at({(ChainId.ETHEREUM, USDT)})

        self.assertEqual(tokens[(ChainId.ETHEREUM, USDT)].pk, contract.pk)
        self.assertEqual(Contract.objects.count(), 1)


class SaveTokensTests(TestCase):
    def test_stores_every_token_and_answers_how_many(self):
        saved = services.save_tokens([token(at=address(n)) for n in range(1, 4)])

        self.assertEqual(saved, 3)
        self.assertEqual(Token.objects.count(), 3)

    def test_a_stored_chain_and_address_is_updated_not_duplicated(self):
        services.save_tokens([token(name="Tether")])

        services.save_tokens([token(name="Tether USD")])

        self.assertEqual(Token.objects.count(), 1)
        self.assertEqual(Token.objects.get().name, "Tether USD")

    def test_when_two_tokens_name_one_address_the_first_is_stored(self):
        saved = services.save_tokens(
            [token(coingecko_id="first"), token(coingecko_id="second", at="0x" + USDT[2:].upper())]
        )

        self.assertEqual(saved, 1)
        self.assertEqual(Token.objects.get().coingecko_id, "first")

    def test_saving_again_leaves_verification_and_functions_alone(self):
        services.save_tokens([token()])
        transfer = FunctionSignature.objects.create(
            id=1, hex_signature="0xa9059cbb", name="transfer"
        )
        row = Token.objects.get()
        row.contract_is_verified = True
        row.save()
        row.functions.add(transfer)

        services.save_tokens([token(name="Tether USD")])

        row = Token.objects.get()
        self.assertEqual(row.name, "Tether USD")
        self.assertTrue(row.contract_is_verified)
        self.assertEqual(list(row.functions.all()), [transfer])

    def test_nothing_to_save_stores_nothing(self):
        self.assertEqual(services.save_tokens([]), 0)
        self.assertEqual(Token.objects.count(), 0)


class LoadTokensTests(TestCase):
    def test_a_coin_is_a_row_per_chain_it_is_deployed_on(self):
        output = load(
            [coin("tether", ethereum=USDT, binance_smart_chain=address(1), arbitrum_one=address(2))]
        )

        self.assertEqual(
            sorted(Token.objects.values_list("chain", "address")),
            [
                (ChainId.ETHEREUM, USDT),
                (ChainId.BNB_SMART_CHAIN, address(1)),
                (ChainId.ARBITRUM_ONE, address(2)),
            ],
        )
        self.assertIn("Loaded 3 token address(es) from 1 of 1 coin(s).", output)

    def test_stores_the_name_and_coingecko_id_of_each_row(self):
        load([coin("tether", name="Tether", ethereum=USDT)])

        row = Token.objects.get(chain=ChainId.ETHEREUM, address=USDT)
        self.assertEqual(row.name, "Tether")
        self.assertEqual(row.coingecko_id, "tether")

    def test_a_loaded_row_starts_unverified_with_no_functions(self):
        load([coin("tether", ethereum=USDT)])

        row = Token.objects.get()
        self.assertIsNone(row.contract_is_verified)
        self.assertEqual(list(row.functions.all()), [])

    def test_a_platform_without_an_evm_chain_id_is_skipped(self):
        load([coin("tether", ethereum=USDT, solana="Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB")])

        self.assertEqual(list(Token.objects.values_list("chain", flat=True)), [ChainId.ETHEREUM])

    def test_a_native_coin_with_no_platforms_stores_nothing(self):
        output = load([coin("bitcoin")])

        self.assertEqual(Token.objects.count(), 0)
        self.assertIn("Loaded 0 token address(es) from 1 of 1 coin(s).", output)

    def test_an_address_that_is_not_an_evm_address_is_skipped(self):
        load([coin("sei-token", sei_v2=SEI_COSMOS)])

        self.assertEqual(Token.objects.count(), 0)

    def test_an_address_is_stored_lowercase(self):
        load([coin("tether", ethereum="0x" + USDT[2:].upper())])

        self.assertEqual(Token.objects.get().address, USDT)

    def test_a_name_or_id_the_file_wrote_as_a_literal_is_stored_as_its_text(self):
        load([coin(True, name=69420, ethereum=USDT)])

        row = Token.objects.get()
        self.assertEqual(row.coingecko_id, "true")
        self.assertEqual(row.name, "69420")

    def test_when_two_coins_claim_one_address_the_earlier_keeps_it(self):
        load([coin("first", ethereum=USDT), coin("second", ethereum=USDT)])

        self.assertEqual(Token.objects.get().coingecko_id, "first")

    def test_a_limit_loads_only_that_many_coins_from_the_top_of_the_file(self):
        coins = [coin(f"c{n}", base=address(n)) for n in (9, 4, 7, 2)]

        output = load(coins, limit=2)

        self.assertEqual(sorted(Token.objects.values_list("coingecko_id", flat=True)), ["c4", "c9"])
        self.assertIn("Loaded 2 token address(es) from 2 of 4 coin(s).", output)

    def test_a_limit_past_the_end_of_the_file_loads_it_all(self):
        load([coin("tether", ethereum=USDT)], limit=50)

        self.assertEqual(Token.objects.count(), 1)

    def test_a_limit_of_zero_loads_nothing(self):
        load([coin("tether", ethereum=USDT)], limit=0)

        self.assertEqual(Token.objects.count(), 0)

    def test_a_negative_limit_is_refused(self):
        with self.assertRaises(CommandError):
            load([coin("tether", ethereum=USDT)], limit=-1)

    def test_a_second_run_updates_rather_than_duplicates(self):
        load([coin("tether", name="Tether", ethereum=USDT)])

        load([coin("tether", name="Tether USD", ethereum=USDT)])

        self.assertEqual(Token.objects.count(), 1)
        self.assertEqual(Token.objects.get().name, "Tether USD")

    def test_a_missing_file_is_reported(self):
        with self.assertRaises(CommandError):
            call_command("load_tokens", path="/nonexistent/tokens.json")

        self.assertEqual(Token.objects.count(), 0)

    def test_the_raw_data_file_loads(self):
        out = io.StringIO()
        call_command("load_tokens", limit=100, stdout=out)

        self.assertIn("from 100 of 10000 coin(s).", out.getvalue())
        self.assertTrue(Token.objects.filter(chain=ChainId.ETHEREUM, address=USDT).exists())
