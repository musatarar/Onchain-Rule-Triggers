"""The SPA's page shells: one public Django page per React route."""

import re
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase

SHELLS = {
    "/signin": "Sign in · Locked In",
    "/auth/consume": "Signing you in · Locked In",
    "/journal/": "Match journal · Onchain Rule Triggers",
    "/circuits/": "Circuits · Onchain Rule Triggers",
    "/circuits/new/": "New circuit · Onchain Rule Triggers",
    "/circuits/8/": "Edit circuit · Onchain Rule Triggers",
}


class PageShellTests(TestCase):
    def test_every_shell_is_public_and_mints_a_csrf_cookie(self):
        # The shells render an empty #root; access control is RequireAuth.tsx, and
        # @login_required would replace its designed sign-in redirect with a 302.
        for url in SHELLS:
            with self.subTest(url=url):
                response = Client().get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn("csrftoken", response.cookies)
                self.assertContains(response, '<div id="root"></div>')

    def test_each_shell_is_titled_with_its_page(self):
        for url, title in SHELLS.items():
            with self.subTest(url=url):
                self.assertContains(Client().get(url), f"<title>{title}</title>")

    def test_the_root_redirects_to_the_journal_where_signing_in_lands(self):
        response = Client().get("/")
        self.assertRedirects(response, "/journal/", fetch_redirect_response=False)

    def test_every_react_route_has_a_shell(self):
        main = Path(settings.BASE_DIR, "frontend", "src", "main.tsx").read_text()
        routes = re.findall(r'<Route path="([^"]+)"', main)
        self.assertEqual(len(routes), 6)
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(Client().get(route.replace(":id", "8")).status_code, 200)
