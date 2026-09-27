"""Tests for username/password accounts: registration, login, and the
boundary with magic-link users."""

from __future__ import annotations

import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from project.app.services import accounts, login_links
from project.app.services.accounts import RegisterOutcome
from project.app.tests.tests_auth import ALLOWED, AuthAPITestCase, scoped_rate
from project.app.throttling import PasswordLoginUsernameRateThrottle

PASSWORD = "correct-horse-battery"


class UsernameRuleTests(TestCase):
    def test_a_plain_username_is_accepted(self):
        self.assertIsNone(accounts.username_error("satoshi_n.1-a"))

    def test_too_short_is_rejected(self):
        self.assertIsNotNone(accounts.username_error("ab"))

    def test_at_sign_is_rejected_so_email_keyed_accounts_cannot_be_claimed(self):
        self.assertIsNotNone(accounts.username_error(ALLOWED))

    def test_a_trailing_newline_is_rejected(self):
        self.assertIsNotNone(accounts.username_error("satoshi\n"))


class RegisterServiceTests(TestCase):
    def test_password_is_hashed(self):
        result = accounts.register_user("satoshi", PASSWORD)

        self.assertIs(result.outcome, RegisterOutcome.OK)
        self.assertNotEqual(result.user.password, PASSWORD)
        self.assertTrue(result.user.check_password(PASSWORD))

    def test_a_case_variant_of_a_taken_username_is_taken(self):
        accounts.register_user("satoshi", PASSWORD)

        self.assertIs(
            accounts.register_user("Satoshi", PASSWORD).outcome, RegisterOutcome.USERNAME_TAKEN
        )

    def test_weak_passwords_are_reported_by_the_configured_validators(self):
        self.assertTrue(accounts.password_errors("12345678", username="satoshi"))
        self.assertEqual(accounts.password_errors(PASSWORD, username="satoshi"), [])


class RegisterEndpointTests(AuthAPITestCase):
    URL = "/api/auth/register/"

    def test_register_creates_the_user_and_signs_in(self):
        resp = self.client.post(
            self.URL,
            {"username": "satoshi", "password": PASSWORD, "email": "Sat@Example.com"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["username"], "satoshi")
        self.assertEqual(resp.data["email"], "sat@example.com")
        self.assertTrue(resp.data["authenticated"])
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

    def test_email_is_optional(self):
        resp = self.client.post(
            self.URL, {"username": "satoshi", "password": PASSWORD}, format="json"
        )

        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.data["email"])
        self.assertEqual(get_user_model().objects.get(username="satoshi").email, "")

    def test_taken_username_is_409(self):
        accounts.register_user("satoshi", PASSWORD)

        resp = self.client.post(
            self.URL, {"username": "SATOSHI", "password": PASSWORD}, format="json"
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["code"], "username_taken")

    def test_invalid_username_is_400(self):
        for username in ["", "ab", ALLOWED, "has space"]:
            with self.subTest(username=username):
                resp = self.client.post(
                    self.URL, {"username": username, "password": PASSWORD}, format="json"
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.data["code"], "invalid_username")

    def test_weak_password_is_400_with_the_validator_messages(self):
        resp = self.client.post(
            self.URL, {"username": "satoshi", "password": "password"}, format="json"
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "weak_password")
        self.assertIn("too common", resp.data["detail"])
        self.assertFalse(get_user_model().objects.filter(username="satoshi").exists())

    def test_malformed_email_is_400(self):
        resp = self.client.post(
            self.URL,
            {"username": "satoshi", "password": PASSWORD, "email": "nope"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_email")

    @scoped_rate("auth_register_ip", "2/hour")
    def test_registration_is_capped_per_ip(self):
        for name in ["user-one", "user-two"]:
            body = {"username": name, "password": PASSWORD}
            self.assertEqual(self.client.post(self.URL, body, format="json").status_code, 201)

        body = {"username": "user-three", "password": PASSWORD}
        resp = self.client.post(self.URL, body, format="json")

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.data["code"], "rate_limited")


class PasswordLoginEndpointTests(AuthAPITestCase):
    URL = "/api/auth/login/"

    def setUp(self):
        super().setUp()
        accounts.register_user("satoshi", PASSWORD)

    def test_correct_credentials_sign_in(self):
        resp = self.client.post(
            self.URL, {"username": " satoshi ", "password": PASSWORD}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["username"], "satoshi")
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

    def test_wrong_password_and_unknown_user_answer_identically(self):
        wrong = self.client.post(
            self.URL, {"username": "satoshi", "password": "nope"}, format="json"
        )
        unknown = self.client.post(
            self.URL, {"username": "nobody", "password": PASSWORD}, format="json"
        )

        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.data, unknown.data)
        self.assertEqual(wrong.data["code"], "invalid_credentials")

    def test_missing_fields_are_invalid_credentials(self):
        resp = self.client.post(self.URL, {}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_credentials")

    def test_inactive_user_cannot_sign_in(self):
        get_user_model().objects.filter(username="satoshi").update(is_active=False)

        resp = self.client.post(
            self.URL, {"username": "satoshi", "password": PASSWORD}, format="json"
        )

        self.assertEqual(resp.data["code"], "invalid_credentials")

    @override_settings(LOGIN_ALLOWED_EMAILS={ALLOWED})
    def test_a_magic_link_user_has_no_password_to_guess(self):
        issued = login_links.issue_login_link(ALLOWED)
        self.client.post("/api/auth/consume/", {"token": issued.raw_token}, format="json")
        self.client.post("/api/auth/logout/", {}, format="json")

        resp = self.client.post(self.URL, {"username": ALLOWED, "password": ""}, format="json")

        self.assertEqual(resp.data["code"], "invalid_credentials")

    @scoped_rate("auth_login_username", "2/hour")
    def test_guessing_one_account_is_capped_across_ips(self):
        for ip in ["10.0.0.1", "10.0.0.2"]:
            body = {"username": "satoshi", "password": "nope"}
            resp = self.client.post(self.URL, body, format="json", REMOTE_ADDR=ip)
            self.assertEqual(resp.status_code, 400)

        resp = self.client.post(
            self.URL,
            {"username": "Satoshi", "password": PASSWORD},
            format="json",
            REMOTE_ADDR="10.0.0.3",
        )

        self.assertEqual(resp.status_code, 429)
        self.assertTrue(resp.data["detail"].startswith("Too many sign-in attempts."))

    @scoped_rate("auth_login_ip", "2/hour")
    def test_login_is_capped_per_ip(self):
        for name in ["a-user", "b-user"]:
            body = {"username": name, "password": "nope"}
            self.assertEqual(self.client.post(self.URL, body, format="json").status_code, 400)

        resp = self.client.post(self.URL, {"username": "c-user", "password": "x"}, format="json")

        self.assertEqual(resp.status_code, 429)


class UsernameThrottleCacheKeyTests(TestCase):
    def test_cache_key_is_a_hash_of_the_lowercased_username(self):
        raw = APIRequestFactory().post("/api/auth/login/", {"username": " Satoshi "}, format="json")
        key = PasswordLoginUsernameRateThrottle().get_cache_key(
            Request(raw, parsers=[JSONParser()]), None
        )

        self.assertNotIn("atoshi", key)
        self.assertIn(hashlib.sha256(b"satoshi").hexdigest(), key)
