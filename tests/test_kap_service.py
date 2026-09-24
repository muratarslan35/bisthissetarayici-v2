import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import database
import kap_service

TR_TZ = ZoneInfo("Europe/Istanbul")


class FakeResponse:
    def __init__(self, body, url, status=200, content_type="text/html"):
        self.status_code = status
        self.url = url
        self.text = body
        self.content = body.encode("utf-8")
        self.headers = {"content-type": content_type}

    def json(self):
        raise ValueError("not json")


class KapServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "kap-test.db"
        database.init_db()
        kap_service.init_kap_store()
        kap_service._LAST_ATTEMPT.update({
            "KAP_RSS": 0.0,
            "KAP_HTML": 0.0,
            "KAP_API": 0.0,
        })

    def tearDown(self):
        database.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_official_host_validation_rejects_third_party(self):
        self.assertTrue(kap_service._official_kap_url("https://www.kap.org.tr/tr/Bildirim/1"))
        self.assertFalse(kap_service._official_kap_url("https://nitter.net/kapbildirim/rss"))

    def test_rss_parser_accepts_verified_official_disclosure(self):
        rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <title>KAP</title>
          <item>
            <title>GESAN (GESAN) - Yeni İş İlişkisi</title>
            <link>https://www.kap.org.tr/tr/Bildirim/123456</link>
            <pubDate>Thu, 24 Sep 2026 12:00:00 +0300</pubDate>
            <description>GESAN yeni sözleşme ve yatırım açıklaması</description>
          </item>
        </channel></rss>"""
        response = FakeResponse(
            rss,
            "https://www.kap.org.tr/tr/rss/all",
            content_type="application/rss+xml",
        )

        with patch.object(kap_service.SESSION, "get", return_value=response):
            events = kap_service.fetch_rss_events(["GESAN.IS"])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kap_id"], "123456")
        self.assertEqual(events[0]["symbol"], "GESAN.IS")
        self.assertTrue(events[0]["trade_relevant"])
        self.assertTrue(events[0]["time_verified"])

    def test_html_reconciliation_parser_extracts_id_symbol_and_time(self):
        html = """
        <html><body><h1>Bildirim Sorguları</h1>
        <table><tr>
          <td></td><td>1</td><td>24.09.2026 12:01</td><td>GESAN</td>
          <td>Girişim Elektrik</td><td>Özel Durum Açıklaması</td>
          <td>Yeni İş İlişkisi</td><td>GESAN yeni sözleşme</td>
          <td><a href="/tr/Bildirim/123456">Detay</a></td>
        </tr></table></body></html>
        """
        response = FakeResponse(
            html,
            "https://www.kap.org.tr/tr/bildirim-sorgu-sonuc?cat=6&cmp=Y&slf=ALL&srcbar=Y",
        )

        with patch.object(kap_service.SESSION, "get", return_value=response):
            events = kap_service.fetch_html_events(["GESAN.IS"])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kap_id"], "123456")
        self.assertEqual(events[0]["symbol"], "GESAN.IS")
        self.assertTrue(events[0]["time_verified"])
        self.assertEqual(events[0]["event_time"].hour, 12)

    def test_poll_persists_once_and_suppresses_duplicate_alert(self):
        event = {
            "kap_id": "999001",
            "symbol": "GESAN.IS",
            "title": "Yeni İş İlişkisi",
            "summary": "GESAN sözleşme",
            "link": "https://www.kap.org.tr/tr/Bildirim/999001",
            "event_time": datetime.now(TR_TZ),
            "score": 24,
            "trade_relevant": True,
            "time_verified": True,
        }

        with patch("kap_service.fetch_rss_events", return_value=[event]), \
             patch("kap_service.fetch_html_events", return_value=[]):
            first = kap_service.poll_kap(["GESAN.IS"], force=True)
            second = kap_service.poll_kap(["GESAN.IS"], force=True)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

        health = kap_service.get_kap_health()
        self.assertEqual(health["verified_events_24h"], 1)


if __name__ == "__main__":
    unittest.main()
