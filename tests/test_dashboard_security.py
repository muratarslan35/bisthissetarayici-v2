import unittest

from app import app


class DashboardSecurityTests(unittest.TestCase):
    def test_dashboard_api_requires_authenticated_session(self):
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.get("/api/dashboard", follow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303, 307, 308))
        self.assertTrue(response.headers.get("Location", "").endswith("/login"))


if __name__ == "__main__":
    unittest.main()
