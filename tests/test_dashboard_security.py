import unittest

from app import app


class DashboardSecurityTests(unittest.TestCase):
    def test_dashboard_api_requires_authenticated_session(self):
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.get("/api/dashboard", follow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303, 307, 308))
        self.assertTrue(response.headers.get("Location", "").endswith("/login"))

    def test_signal_card_test_screen_is_admin_only(self):
        app.config["TESTING"] = True
        with app.test_client() as client:
            page = client.get("/admin/test-signal-card")
            image = client.get("/admin/test-signal-card/image")
        self.assertEqual(page.status_code, 302)
        self.assertEqual(image.status_code, 302)
        self.assertIn("/login?next=%2Fadmin%2Ftest-signal-card", page.headers["Location"])
        self.assertIn("/login?next=%2Fadmin%2Ftest-signal-card%2Fimage", image.headers["Location"])

    def test_login_page_preserves_safe_admin_return_path(self):
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.get("/login?next=/admin/test-signal-card")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="next" value="/admin/test-signal-card"', response.data)


if __name__ == "__main__":
    unittest.main()
