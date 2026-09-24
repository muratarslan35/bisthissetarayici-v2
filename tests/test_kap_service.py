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
    def __init__(self, payload, url, status=200, content_type="application/json"):
        self.status_code = status
        self.url = url
        self._payload = payload
        self.headers = {"content-type": content_type}
        self.text = payload if isinstance(payload, str) else ""

    def json(self):
        return self._payload


class KapServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "kap-test.db"
        database.init_db()
        kap_service.init_kap_store()

        for key in kap_service._LAST_ATTEMPT:
            kap_service._LAST_ATTEMPT[key] = 0.0
            kap_service._NEXT_ALLOWED[key] = 0.0
            kap_service._FAILURES[key] = 0
        kap_service._BOOTSTRAPPED.clear()

    def tearDown(self):
        database.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_official_host_validation_rejects_third_party(self):
        self.assertTrue(
            kap_service._official_kap_url(
                "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"
            )
        )
        self.assertFalse(
            kap_service._official_kap_url("https://nitter.net/kapbildirim/rss")
        )

    def test_public_api_parser_uses_structured_symbol_and_publish_time(self):
        payload = [{
            "publishDate": "24.09.2026 12:01:35",
            "kapTitle": "GİRİŞİM ELEKTRİK SANAYİ TAAHHÜT VE TİCARET A.Ş.",
            "disclosureClass": "ODA",
            "disclosureType": "ODA",
            "disclosureCategory": "ODA",
            "summary": "Mardin-2 GES Projesi Sözleşmesi",
            "subject": "Yeni İş İlişkisi",
            "relatedStocks": "GESAN",
            "disclosureIndex": 1666001,
            "stockCodes": "GESAN",
        }]
        response = FakeResponse(
            payload,
            "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria",
        )

        with patch.object(kap_service.SESSION, "post", return_value=response):
            events = kap_service.fetch_public_api_events(
                ["GESAN.IS"],
                member_type="IGS",
                day=datetime(2026, 9, 24).date(),
            )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kap_id"], "1666001")
        self.assertEqual(event["symbol"], "GESAN.IS")
        self.assertEqual(event["subject"], "Yeni İş İlişkisi")
        self.assertTrue(event["trade_relevant"])
        self.assertEqual(event["event_time"].hour, 12)
        self.assertEqual(event["discovery_source"], "KAP_API_IGS")

    def test_detail_api_second_level_verification(self):
        event = {
            "kap_id": "1666001",
            "symbol": "GESAN.IS",
            "link": "https://www.kap.org.tr/tr/Bildirim/1666001",
        }
        payload = [{
            "disclosure": {
                "disclosureBasic": {
                    "disclosureIndex": 1666001,
                    "stockCode": "GESAN",
                    "relatedStocks": "GESAN",
                }
            }
        }]
        response = FakeResponse(
            payload,
            "https://www.kap.org.tr/tr/api/notification/attachment-detail/1666001",
        )

        with patch.object(kap_service.SESSION, "get", return_value=response):
            self.assertTrue(kap_service.verify_detail(event))


    def test_unverified_detail_never_reaches_trade_cache(self):
        event = {
            "kap_id": "999002",
            "symbol": "GESAN.IS",
            "company_title": "GİRİŞİM ELEKTRİK",
            "disclosure_class": "ODA",
            "disclosure_type": "ODA",
            "disclosure_category": "ODA",
            "subject": "Yeni İş İlişkisi",
            "summary": "GESAN sözleşme",
            "link": "https://www.kap.org.tr/tr/Bildirim/999002",
            "event_time": datetime.now(TR_TZ),
            "score": 24,
            "trade_relevant": True,
            "discovery_source": "KAP_API_IGS",
            "raw_item": {"disclosureIndex": 999002},
        }

        def fake_fetch(symbols, member_type="IGS", day=None):
            return [dict(event)] if member_type == "IGS" else []

        with patch(
            "kap_service.fetch_public_api_events",
            side_effect=fake_fetch,
        ), patch("kap_service.verify_detail", return_value=False):
            emitted = kap_service.poll_kap(["GESAN.IS"], force=True)

        self.assertEqual(emitted, [])
        cached = kap_service.recent_kap_cache(minutes=30)
        self.assertNotIn("GESAN.IS", cached)

        health = kap_service.get_kap_health()
        self.assertEqual(health["verified_events_24h"], 1)
        self.assertFalse(health["recent"][0]["detail_verified"])

    def test_poll_persists_once_and_suppresses_duplicate_alert(self):
        event = {
            "kap_id": "999001",
            "symbol": "GESAN.IS",
            "company_title": "GİRİŞİM ELEKTRİK",
            "disclosure_class": "ODA",
            "disclosure_type": "ODA",
            "disclosure_category": "ODA",
            "subject": "Yeni İş İlişkisi",
            "summary": "GESAN sözleşme",
            "link": "https://www.kap.org.tr/tr/Bildirim/999001",
            "event_time": datetime.now(TR_TZ),
            "score": 24,
            "trade_relevant": True,
            "discovery_source": "KAP_API_IGS",
            "raw_item": {"disclosureIndex": 999001},
        }

        def fake_fetch(symbols, member_type="IGS", day=None):
            return [dict(event)] if member_type == "IGS" else []

        with patch(
            "kap_service.fetch_public_api_events",
            side_effect=fake_fetch,
        ), patch("kap_service.verify_detail", return_value=True):
            first = kap_service.poll_kap(["GESAN.IS"], force=True)
            second = kap_service.poll_kap(["GESAN.IS"], force=True)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

        health = kap_service.get_kap_health()
        self.assertEqual(health["verified_events_24h"], 1)

        cached = kap_service.recent_kap_cache(minutes=30)
        self.assertIn("GESAN.IS", cached)
        self.assertTrue(cached["GESAN.IS"]["verified"])


if __name__ == "__main__":
    unittest.main()
