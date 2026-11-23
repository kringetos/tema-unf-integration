import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tema_api.server import Journal, handler
from tema_api.service import Service
from tema_api.upstream import APIError, Client, Result, literal


GUID = "a86a0a42-6b8f-11d8-b0ec-0050ba2342c8"


class FakeOData:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def get(self, entity, query=None):
        self.calls.append(("GET", entity, query))
        if "(guid'" in entity:
            return self.rows[0]
        return {"value": self.rows, "odata.count": len(self.rows)}

    def request(self, method, path, query=None, body=None, headers=None):
        self.calls.append((method, path, query, body, headers))
        return Result(201, {"Code": "000002", "Ref_Key": GUID})


class FakeBridge:
    def __init__(self):
        self.calls = []

    def request(self, *args):
        self.calls.append(args)
        return Result(201 if args[0] == "POST" else 200, {"status": "success"})


class TemaServiceTests(unittest.TestCase):
    def test_search_escapes_odata_literal_and_uses_v3(self):
        upstream = FakeOData()
        result = Service(upstream).dispatch("GET", "/api/v1/counterparties", {"search": "O'Brien", "page": "2", "limit": "10"})
        query = upstream.calls[0][2]
        self.assertIn("substringof('O''Brien', Description)", query["$filter"])
        self.assertEqual(query["$skip"], 10)
        self.assertEqual(query["$inlinecount"], "allpages")
        self.assertEqual(result.body["total"], 0)

    def test_deleted_and_folders_excluded_from_lists(self):
        upstream = FakeOData()
        Service(upstream).dispatch("GET", "/nomenclatures")
        self.assertIn("DeletionMark eq false", upstream.calls[0][2]["$filter"])
        self.assertIn("IsFolder eq false", upstream.calls[0][2]["$filter"])

    def test_guid_and_number_resolution_never_choose_random_duplicate(self):
        upstream = FakeOData([{"Ref_Key": GUID}, {"Ref_Key": GUID}])
        with self.assertRaises(APIError) as error:
            Service(upstream).resolve("invoice", "000001")
        self.assertEqual(error.exception.code, "AMBIGUOUS_ID")

    def test_no_deleted_guid_result(self):
        with self.assertRaises(APIError) as error:
            Service(FakeOData([{"DeletionMark": True}])).resolve("counterparty", GUID)
        self.assertEqual(error.exception.status, 404)

    def test_catalog_mapping_uses_real_unf_types(self):
        upstream = FakeOData()
        result = Service(upstream).dispatch("POST", "/1cTema/hs/api/v1/counterparty", body={
            "name": "Тест", "type": "ЮридическоеЛицо", "ogrn": "1027700000001", "is_active": False,
            "roles": {"is_customer": True}})
        payload = upstream.calls[-1][3]
        self.assertEqual(payload["РегистрационныйНомер"], "1027700000001")
        self.assertTrue(payload["Недействителен"])
        self.assertNotIn("DeletionMark", payload)
        self.assertEqual(result.status, 201)

    def test_contact_write_is_atomic_bridge_operation(self):
        upstream, bridge = FakeOData(), FakeBridge()
        Service(upstream, bridge, {"contact_writes_enabled": True}).dispatch("POST", "/counterparty", body={
            "name": "Тест", "type": "ЮридическоеЛицо", "contact_info": [{"type": "Телефон", "value": "+70000000000"}]})
        self.assertEqual(upstream.calls, [])
        self.assertEqual(bridge.calls[0][1], "/counterparty")

    def test_contacts_are_disabled_by_default_after_platform_crash(self):
        upstream, bridge = FakeOData(), FakeBridge()
        with self.assertRaises(APIError) as error:
            Service(upstream, bridge).dispatch("POST", "/counterparty", body={
                "name": "Тест", "type": "ЮридическоеЛицо", "contact_info": []})
        self.assertEqual(error.exception.code, "CONTACT_WRITES_DISABLED")
        self.assertEqual(upstream.calls, [])
        self.assertEqual(bridge.calls, [])

    def test_financial_writes_use_native_bridge(self):
        bridge = FakeBridge()
        Service(FakeOData(), bridge).dispatch("POST", "/invoice", body={"order_id": "000001", "date": "2026-05-06"}, key="invoice-1")
        self.assertEqual(bridge.calls[0][1], "/invoice")
        self.assertEqual(bridge.calls[0][4], {"Idempotency-Key": "invoice-1"})

    def test_missing_bridge_is_explicit_failure(self):
        with self.assertRaises(APIError) as error:
            Service(FakeOData()).dispatch("GET", "/invoice/000001")
        self.assertEqual(error.exception.code, "BRIDGE_NOT_CONFIGURED")

    def test_date_to_includes_entire_day(self):
        upstream = FakeOData()
        Service(upstream).dispatch("GET", "/orders", {"date_from": "2026-05-01", "date_to": "2026-05-05"})
        self.assertIn("Date lt datetime'2026-05-06T00:00:00'", upstream.calls[0][2]["$filter"])

    def test_validation_rejects_bad_input_before_writing(self):
        upstream = FakeOData()
        service = Service(upstream, FakeBridge())
        cases = [("GET", "/orders", {"limit": "0"}, None),
                 ("GET", "/orders", {"page": "1.5"}, None),
                 ("GET", "/orders", {"date_from": "2026-06-01", "date_to": "2026-05-01"}, None),
                 ("POST", "/counterparty", {}, {"name": "X", "type": "ЮридическоеЛицо", "is_active": "false"}),
                 ("POST", "/invoice/000001/payment", {}, {"date": "2026-05-01", "amount": "NaN", "currency": "RUB", "payment_method": "bank_transfer"}),
                 ("POST", "/order", {}, {"date": "2026-05-01", "customer": {"id": "1"}, "items": []})]
        for method, path, query, body in cases:
            with self.subTest(path=path, body=body), self.assertRaises(APIError) as error:
                service.dispatch(method, path, query, body)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(upstream.calls, [])

    def test_invoice_payment_filter_applies_before_pagination_in_bridge(self):
        bridge = FakeBridge()
        Service(FakeOData(), bridge).dispatch("GET", "/invoices", {"status": "partially", "page": "2"})
        self.assertEqual(bridge.calls[0][:3], ("GET", "/invoices", {"status": "partially", "page": "2"}))

    def test_all_pdf_endpoints_forward_binary(self):
        bridge = FakeBridge()
        for path in ["/invoice/1/pdf", "/act/1/pdf", "/order/1/kp/pdf"]:
            Service(FakeOData(), bridge, {"print_commands": {"kp": "КоммерческоеПредложение", "specification": "Спецификация"}}).dispatch("GET", path)
            self.assertEqual(bridge.calls[-1][1], path)


class JournalTests(unittest.TestCase):
    def test_replay_conflict_and_pending_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.sqlite3"
            journal = Journal(path)
            self.assertIsNone(journal.reserve("key", "fingerprint"))
            with self.assertRaises(APIError) as error:
                Journal(path).reserve("key", "fingerprint")
            self.assertEqual(error.exception.code, "WRITE_OUTCOME_UNKNOWN")
            journal.complete("key", Result(201, {"id": "1"}))
            self.assertEqual(Journal(path).reserve("key", "fingerprint").body, {"id": "1"})
            with self.assertRaises(APIError):
                journal.reserve("key", "different")


class HTTPTests(unittest.TestCase):
    def test_odata_wire_encoding_preserves_options_and_uses_percent_spaces(self):
        paths = []

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"value": []}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            Client("http://127.0.0.1:" + str(server.server_port)).get("Catalog_Test", {
                "$filter": "Code eq '796' and Description eq 'A&B'", "$top": 2})
            self.assertIn("$filter=Code%20eq%20'796'%20and", paths[0])
            self.assertIn("A%26B", paths[0])
            self.assertNotIn("+", paths[0])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_widget_cors_preflight_and_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(FakeOData(), settings={"cors_allowed_origins": ["https://*.amocrm.ru"]})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, "x" * 40, Journal(Path(directory) / "journal.sqlite3")))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                headers = {"Origin": "https://tema.amocrm.ru", "Access-Control-Request-Method": "PATCH",
                           "Access-Control-Request-Headers": "authorization, content-type, idempotency-key"}
                connection.request("OPTIONS", "/api/v1/counterparty/test", headers=headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 204)
                self.assertEqual(response.getheader("Access-Control-Allow-Origin"), headers["Origin"])
                response.read()
                connection.request("GET", "/api/v1/counterparties", headers={"Origin": headers["Origin"]})
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                self.assertEqual(response.getheader("Access-Control-Allow-Origin"), headers["Origin"])
                response.read()
                for origin in ["https://tema.amocrm.ru.attacker.example", "https://attackeramocrm.ru", "http://tema.amocrm.ru", "null"]:
                    connection.request("OPTIONS", "/api/v1/counterparties", headers={**headers, "Origin": origin})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 403)
                    self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
                    response.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join()

    def test_auth_validation_and_idempotent_post_over_http(self):
        with tempfile.TemporaryDirectory() as directory:
            upstream = FakeOData()
            token = "x" * 40
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(Service(upstream), token, Journal(Path(directory) / "journal.sqlite3")))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request("GET", "/api/v1/counterparties")
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
                headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Idempotency-Key": "create-1"}
                body = json.dumps({"name": "Test", "type": "ЮридическоеЛицо"})
                for _ in range(2):
                    connection.request("POST", "/api/v1/counterparty", body, headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 201)
                    self.assertTrue(response.getheader("X-Request-ID"))
                    response.read()
                self.assertEqual(sum(call[0] == "POST" for call in upstream.calls), 1)
                connection.request("GET", "/api/v1/counterparties?page=1&page=2", headers=headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
