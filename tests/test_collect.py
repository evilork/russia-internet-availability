"""Tests for scripts/collect.py and the published files.

Run: python3 -I -m unittest discover -s tests
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import collect  # noqa: E402  (path set above)

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
README = """# Title

<!-- summary:en:start -->
No data yet.
<!-- summary:en:end -->

## По-русски

<!-- summary:ru:start -->
Данных пока нет.
<!-- summary:ru:end -->
"""


def document(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": collect.SCHEMA,
        "license": "CC-BY-4.0",
        "checked_at": "2026-11-03T07:12:05.838Z",
        "points": [{"city": "Moscow", "network": "Timeweb"}],
        "services": [
            {"slug": "youtube", "name": "YouTube", "host": "www.youtube.com", "verdict": "blocked",
             "ok": 0, "refused": 0, "blocked": 7, "dns": 0, "counted": 7, "outcomes": ["blocked"]},
            {"slug": "x", "name": "X (Twitter)", "host": "x.com", "verdict": "partial",
             "ok": 3, "refused": 0, "blocked": 4, "dns": 0, "counted": 7, "outcomes": ["ok"]},
            {"slug": "gemini", "name": "Gemini", "host": "gemini.google.com", "verdict": None,
             "ok": 0, "refused": 0, "blocked": 0, "dns": 0, "counted": 0, "outcomes": []},
        ],
        "through_proxysvpn": {"checked_at": "2026-11-03T07:31:16.267Z", "protocol": "WataFast", "exit": "DE",
                              "outcomes": {"youtube": "ok", "x": "ok"}},
        "history": [
            {"at": "2026-11-03T07:12:05.838Z", "verdicts": {"youtube": "blocked", "x": "partial"}},
            {"at": "2026-11-03T06:11:40.000Z", "verdicts": {"youtube": "blocked", "x": "works"}},
        ],
    }
    doc.update(over)
    return doc


def raw(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc).encode("utf-8")


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


class ValidateTest(unittest.TestCase):
    def test_a_good_document_gives_hourly_snapshot_and_through_rows(self) -> None:
        p = collect.validate(raw(document()), NOW)
        self.assertEqual(p.services[1], ("x", "X (Twitter)", "x.com"))
        self.assertIn(("2026-11-03T07:12:05Z", "youtube", "blocked"), p.hourly)
        self.assertIn(("2026-11-03T06:11:40Z", "x", "works"), p.hourly)
        self.assertEqual(len(p.hourly), 4)
        self.assertEqual(p.snapshots[0], ("2026-11-03T07:12:05Z", "youtube", "blocked", "0", "0", "7", "0", "7"))
        # A service with no verdict yet gives no snapshot row.
        self.assertEqual([s[1] for s in p.snapshots], ["youtube", "x"])
        self.assertEqual(p.through[0], ("2026-11-03T07:31:16Z", "DE", "youtube", "ok"))

    def test_no_run_yet_and_no_tunnel_give_no_rows(self) -> None:
        p = collect.validate(raw(document(checked_at=None, history=[], through_proxysvpn=None)), NOW)
        self.assertEqual((p.hourly, p.snapshots, p.through), ((), (), ()))
        self.assertEqual(len(p.services), 3)

    def test_refuses_what_it_does_not_publish(self) -> None:
        bad: list[tuple[str, dict[str, Any]]] = [
            ("schema", document(schema="other/1")),
            ("verdict", document(services=[{**document()["services"][0], "verdict": "maybe"}])),
            ("slug", document(services=[{**document()["services"][0], "slug": "../etc"}])),
            ("name", document(services=[{**document()["services"][0], "name": "=HYPERLINK(1)"}])),
            ("host", document(services=[{**document()["services"][0], "host": "10.0.0.1"}])),
            ("count", document(services=[{**document()["services"][0], "blocked": -1}])),
            ("bool count", document(services=[{**document()["services"][0], "ok": True}])),
            ("twice", document(services=[document()["services"][0], document()["services"][0]])),
            ("empty", document(services=[])),
            ("time", document(checked_at="yesterday")),
            ("future", document(checked_at="2026-11-04T09:00:00Z")),
            ("history verdict", document(history=[{"at": "2026-11-03T06:00:00Z", "verdicts": {"x": "nope"}}])),
            ("exit", document(through_proxysvpn={**document()["through_proxysvpn"], "exit": "Germany"})),
            ("outcome", document(through_proxysvpn={**document()["through_proxysvpn"], "outcomes": {"x": "fast"}})),
            ("ipv4", document(points=[{"city": "Moscow", "network": "via 203.0.113.7"}])),
        ]
        for label, doc in bad:
            with self.subTest(label):
                with self.assertRaises(collect.Refused):
                    collect.validate(raw(doc), NOW)
        for blob in (b"not json", b"[]", b"\xff\xfe"):
            with self.assertRaises(collect.Refused):
                collect.validate(blob, NOW)


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "README.md").write_text(README, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stores_once_and_is_idempotent(self) -> None:
        p = collect.validate(raw(document()), NOW)
        self.assertEqual(collect.store(p, self.root), {"hourly": 4, "snapshots": 2, "through": 2})
        self.assertEqual(collect.store(p, self.root), {"hourly": 0, "snapshots": 0, "through": 0})
        hourly = rows(self.root / "data/hourly/2026-11.csv")
        self.assertEqual([r["checked_at"] for r in hourly], sorted(r["checked_at"] for r in hourly))
        self.assertEqual(len(hourly), 4)
        services = rows(self.root / "data/services.csv")
        self.assertEqual([s["service"] for s in services], ["youtube", "x", "gemini"])
        self.assertEqual(services[1]["name"], "X (Twitter)")
        self.assertEqual(json.loads((self.root / "latest.json").read_text())["schema"], collect.SCHEMA)

    def test_rows_go_to_the_month_of_their_check(self) -> None:
        doc = document(history=[
            {"at": "2026-10-31T23:11:00Z", "verdicts": {"youtube": "blocked"}},
            {"at": "2026-11-01T00:11:00Z", "verdicts": {"youtube": "partial"}},
        ])
        collect.store(collect.validate(raw(doc), NOW), self.root)
        self.assertEqual(len(rows(self.root / "data/hourly/2026-10.csv")), 1)
        self.assertEqual(len(rows(self.root / "data/hourly/2026-11.csv")), 3)

    def test_summary_counts_and_readme_tables(self) -> None:
        collect.store(collect.validate(raw(document()), NOW), self.root)
        summary = rows(self.root / "data/summary.csv")
        x = next(r for r in summary if r["service"] == "x")
        self.assertEqual((x["month"], x["checks"], x["works"], x["partial"], x["blocked"]), ("2026-11", "2", "1", "1", "0"))
        through = rows(self.root / "data/summary-through-proxysvpn.csv")
        self.assertEqual([(r["service"], r["checks"], r["ok"]) for r in through], [("youtube", "1", "1"), ("x", "1", "1")])
        text = (self.root / "README.md").read_text(encoding="utf-8")
        # The fixture's direct rows predate HOME_SINCE (data-centre probes): the CSV counts them,
        # the README table does not, and says so with a dash instead of a number.
        self.assertIn("| Service | With ProxysVPN | Without VPN |", text)
        self.assertIn("| YouTube | ✅ opened 100% | — |", text)
        self.assertIn("| X (Twitter) | ✅ opened 100% | — |", text)
        self.assertIn("| Сервис | С ProxysVPN | Без VPN |", text)
        self.assertIn("| YouTube | ✅ открывался 100% | — |", text)
        self.assertIn("Проверок без VPN: 0, через ProxysVPN: 1.", text)
        self.assertNotIn("No data yet.", text)
        self.assertTrue(text.startswith("# Title"))

    def test_readme_counts_only_home_network_rows(self) -> None:
        later = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)
        doc = document(
            checked_at="2026-11-04T02:11:00Z",
            through_proxysvpn={**document()["through_proxysvpn"], "checked_at": "2026-11-04T02:31:00Z"},
            history=[
                {"at": "2026-11-04T02:11:00Z", "verdicts": {"youtube": "blocked", "x": "partial"}},
                {"at": "2026-11-04T01:11:00Z", "verdicts": {"youtube": "blocked", "x": "works"}},
                {"at": "2026-11-04T00:11:00Z", "verdicts": {"youtube": "works", "x": "works"}},
            ],
        )
        collect.store(collect.validate(raw(doc), later), self.root)
        summary = rows(self.root / "data/summary.csv")
        youtube = next(r for r in summary if r["service"] == "youtube")
        self.assertEqual((youtube["checks"], youtube["works"], youtube["blocked"]), ("3", "1", "2"))
        text = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("| YouTube | ✅ opened 100% | did not open 100% |", text)
        self.assertIn("| X (Twitter) | ✅ opened 100% | opened 50%, partly 50% |", text)
        self.assertIn("| YouTube | ✅ открывался 100% | не открывался 100% |", text)
        self.assertIn("Проверок без VPN: 2, через ProxysVPN: 1.", text)
        self.assertIn("home networks", text)
        self.assertIn("домашнем интернете", text)

    def test_home_rows_start_at_the_cutoff(self) -> None:
        self.assertFalse(collect.is_home(("2026-11-04T00:59:59Z", "x", "works")))
        self.assertTrue(collect.is_home((collect.HOME_SINCE, "x", "works")))
        self.assertTrue(collect.is_home(("2026-12-01T00:11:00Z", "x", "works")))

    def test_cells_tick_only_near_every_check_and_dash_without_data(self) -> None:
        words = collect.TABLES["ru"]
        self.assertEqual(collect.vpn_cell(words, None), "—")
        self.assertEqual(collect.vpn_cell(words, ("2026-11", "x", "10", "9", "0", "1", "0", "0")), "✅ открывался 90%")
        self.assertEqual(collect.vpn_cell(words, ("2026-11", "x", "10", "8", "0", "2", "0", "0")), "открывался 80%")
        self.assertEqual(collect.direct_cell(words, ("2026-11", "x", "4", "1", "0", "3", "0", "0")), "открывался 25%, не открывался 75%")
        self.assertEqual(collect.direct_cell(words, ("2026-11", "x", "0", "0", "0", "0", "0", "0")), "—")
        self.assertEqual(collect.direct_cell(words, None), "—")

    def test_table_without_any_row_says_so(self) -> None:
        self.assertEqual(collect.table("en", [], [], []), collect.TABLES["en"]["empty"])

    def test_a_service_the_site_drops_stays_in_the_list(self) -> None:
        collect.store(collect.validate(raw(document()), NOW), self.root)
        later = document(services=document()["services"][:1], history=[], through_proxysvpn=None)
        collect.store(collect.validate(raw(later), NOW), self.root)
        self.assertEqual([s["service"] for s in rows(self.root / "data/services.csv")], ["youtube", "x", "gemini"])

    def test_a_file_with_another_header_is_refused_not_overwritten(self) -> None:
        path = self.root / "data/hourly/2026-11.csv"
        path.parent.mkdir(parents=True)
        path.write_text("when,what\n1,2\n", encoding="utf-8")
        with self.assertRaises(collect.Refused):
            collect.store(collect.validate(raw(document()), NOW), self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "when,what\n1,2\n")

    def test_readme_without_markers_is_refused(self) -> None:
        (self.root / "README.md").write_text("# Title\n", encoding="utf-8")
        with self.assertRaises(collect.Refused):
            collect.store(collect.validate(raw(document()), NOW), self.root)

    def test_names_with_commas_survive_the_csv(self) -> None:
        svc = {**document()["services"][0], "name": 'Odd, "quoted" name'}
        collect.store(collect.validate(raw(document(services=[svc])), NOW), self.root)
        self.assertEqual(rows(self.root / "data/services.csv")[0]["name"], 'Odd, "quoted" name')


class MainTest(unittest.TestCase):
    def test_main_from_a_file_and_a_refused_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(README, encoding="utf-8")
            good, bad = root / "good.json", root / "bad.json"
            doc = document(checked_at=None, history=[], through_proxysvpn=None)
            good.write_bytes(raw(doc))
            bad.write_bytes(b"{}")
            with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(collect.main(["--root", tmp, "--source", str(good)]), 0)
                self.assertEqual(collect.main(["--root", tmp, "--source", str(bad)]), 1)
                self.assertEqual(collect.main(["--root", tmp, "--source", str(root / "missing.json")]), 1)

    def test_fetch_treats_503_as_no_run_and_other_errors_as_refused(self) -> None:
        def raiser(code: int) -> Any:
            def _open(*_a: Any, **_k: Any) -> Any:
                raise urllib.error.HTTPError(collect.SOURCE, code, "x", {}, None)  # type: ignore[arg-type]
            return _open

        with mock.patch("urllib.request.urlopen", raiser(503)):
            self.assertIsNone(collect.fetch())
        with mock.patch("urllib.request.urlopen", raiser(403)):
            with self.assertRaises(collect.Refused):
                collect.fetch()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(collect.Refused):
                collect.fetch()


class PublishedFilesTest(unittest.TestCase):
    def read(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_readme_discloses_the_publisher_and_has_both_tables(self) -> None:
        text = self.read("README.md")
        self.assertIn("Published by ProxysVPN, a paid VPN service", text)
        self.assertIn("## По-русски", text)
        for lang in ("en", "ru"):
            self.assertIn(f"<!-- summary:{lang}:start -->", text)
            self.assertIn(f"<!-- summary:{lang}:end -->", text)
        self.assertIn(collect.SCHEMA, text)

    def test_no_file_claims_independence_speed_or_an_address(self) -> None:
        for name in ("README.md", "CITATION.cff", "scripts/collect.py", ".github/workflows/collect.yml"):
            text = self.read(name).replace("not an independent study", "").replace("не независимое исследование", "")
            self.assertIsNone(re.search(r"\bindependent\b|независим", text, re.I), name)
            self.assertIsNone(re.search(r"\b(fastest|no[- ]logs?|\d+ countries)\b|самый быстр", text, re.I), name)
            self.assertIsNone(collect.IPV4.search(text), name)

    def test_licences_and_citation(self) -> None:
        self.assertTrue(self.read("LICENSE").startswith("Attribution 4.0 International"))
        self.assertIn("MIT License", self.read("LICENSE-CODE"))
        cff = self.read("CITATION.cff")
        self.assertIn("type: dataset", cff)
        self.assertIn("license: CC-BY-4.0", cff)

    def test_the_workflow_tests_before_it_collects_and_pins_checkout(self) -> None:
        text = self.read(".github/workflows/collect.yml")
        self.assertLess(text.index("unittest"), text.index("scripts/collect.py"))
        self.assertRegex(text, r"actions/checkout@[0-9a-f]{40}")
        self.assertIn("contents: write", text)


if __name__ == "__main__":
    unittest.main()
