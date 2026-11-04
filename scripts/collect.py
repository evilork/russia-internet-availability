#!/usr/bin/env python3
"""Store the hourly availability of popular services from Russia as CSV.

Reads https://proxysvpn.com/rabotaet-li.json (schema ``proxysvpn-ru-services/1``),
checks it, and merges it into monthly CSV files under ``data/``:

- ``data/hourly/YYYY-MM.csv``: one row per check and service, the verdict without a VPN;
- ``data/snapshots/YYYY-MM.csv``: the point counts behind each verdict, for the runs this job saw;
- ``data/through-proxysvpn/YYYY-MM.csv``: the same services opened through ProxysVPN;
- ``data/services.csv``: the services and the address each check opens;
- ``data/summary.csv``: per month and service, how many checks ended in each verdict;
- ``data/summary-through-proxysvpn.csv``: the same for the checks through ProxysVPN.

It also writes ``latest.json`` and refreshes the summary tables in ``README.md``.
A row already stored is never stored twice, so the job may run as often as it likes.

Exit codes: 0 stored or nothing new (also when the site has no run yet), 1 refused or unreachable.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Mapping, Sequence

SOURCE: Final[str] = "https://proxysvpn.com/rabotaet-li.json"
SCHEMA: Final[str] = "proxysvpn-ru-services/1"
USER_AGENT: Final[str] = "russia-internet-availability/1 (+https://proxysvpn.com/rabotaet-li)"
TIMEOUT_S: Final[float] = 20.0
MAX_BYTES: Final[int] = 2_000_000
FUTURE_SLACK: Final[timedelta] = timedelta(hours=1)

VERDICTS: Final[tuple[str, ...]] = ("works", "partial", "blocked", "refused", "unknown")
OUTCOMES: Final[frozenset[str]] = frozenset({"ok", "refused", "blocked", "dns", "failed"})
SLUG: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
COUNTRY: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2}$")
HOST: Final[re.Pattern[str]] = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
NAME: Final[re.Pattern[str]] = re.compile(r"^[^\x00-\x1f=+\-@][^\x00-\x1f]{0,59}$")
STAMP: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
IPV4: Final[re.Pattern[str]] = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
MONTH_FILE: Final[re.Pattern[str]] = re.compile(r"^(\d{4}-\d{2})\.csv$")

HOURLY_FIELDS: Final[tuple[str, ...]] = ("checked_at", "service", "verdict")
SNAPSHOT_FIELDS: Final[tuple[str, ...]] = ("checked_at", "service", "verdict", "ok", "refused", "blocked", "dns", "counted")
THROUGH_FIELDS: Final[tuple[str, ...]] = ("checked_at", "exit", "service", "outcome")
SERVICE_FIELDS: Final[tuple[str, ...]] = ("service", "name", "host")
SUMMARY_FIELDS: Final[tuple[str, ...]] = ("month", "service", "checks", *VERDICTS)
OUTCOME_ORDER: Final[tuple[str, ...]] = ("ok", "refused", "blocked", "dns", "failed")
THROUGH_SUMMARY_FIELDS: Final[tuple[str, ...]] = ("month", "service", "checks", *OUTCOME_ORDER)

# «Without VPN» has been measured from Russian home networks only since the
# site's deploy of 2026-11-04 00:27 UTC (first such run 01:11 UTC). Earlier
# rows come from mostly data-centre probes, where blocks often do not apply.
# The CSV files keep every row; the README table counts only the home ones.
HOME_SINCE: Final[str] = "2026-11-04T01:00:00Z"

Row = tuple[str, ...]


class Refused(Exception):
    """The document or a stored file is not one we accept."""


@dataclass(frozen=True)
class Parsed:
    services: tuple[Row, ...]
    hourly: tuple[Row, ...]
    snapshots: tuple[Row, ...]
    through: tuple[Row, ...]
    document: Mapping[str, Any]


# ── Fetch and check ─────────────────────────────────────────────────────────


def fetch(url: str = SOURCE) -> bytes | None:
    """GET the document, at most MAX_BYTES. None when the site has no run yet (HTTP 503)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            if response.status != 200:
                raise Refused(f"HTTP {response.status}")
            body: bytes = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as err:
        err.close()
        if err.code == 503:
            return None
        raise Refused(f"HTTP {err.code}") from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise Refused(f"unreachable: {err}") from err
    if len(body) > MAX_BYTES:
        raise Refused("document larger than 2 MB")
    return body


def stamp(value: Any, where: str, now: datetime) -> str:
    """An ISO time in UTC, normalised to whole seconds: «2026-11-03T07:12:05Z»."""
    if not isinstance(value, str) or not STAMP.match(value):
        raise Refused(f"{where}: not a UTC time")
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise Refused(f"{where}: {err}") from err
    if at > now + FUTURE_SLACK:
        raise Refused(f"{where}: in the future")
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def count(value: Any, where: str) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10_000:
        raise Refused(f"{where}: not a count")
    return str(value)


def slug(value: Any, where: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value):
        raise Refused(f"{where}: not a service id")
    return value


def no_addresses(value: Any, where: str) -> None:
    """The data names services, cities and networks only: an IPv4 address anywhere is refused."""
    if isinstance(value, dict):
        for k, v in value.items():
            no_addresses(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            no_addresses(v, f"{where}[{i}]")
    elif isinstance(value, str) and IPV4.search(value):
        raise Refused(f"an IPv4 address at {where}")


def validate(raw: bytes, now: datetime) -> Parsed:
    """Parse and check one document. O(size of the document)."""
    try:
        doc: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise Refused(f"not JSON: {err}") from err
    if not isinstance(doc, dict):
        raise Refused("not an object")
    if doc.get("schema") != SCHEMA:
        raise Refused(f"schema is {doc.get('schema')!r}, expected {SCHEMA!r}")
    no_addresses(doc, "$")

    raw_services = doc.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise Refused("services missing")
    checked_at = None if doc.get("checked_at") is None else stamp(doc.get("checked_at"), "checked_at", now)

    services: list[Row] = []
    snapshots: list[Row] = []
    seen: set[str] = set()
    for i, s in enumerate(raw_services):
        where = f"services[{i}]"
        if not isinstance(s, dict):
            raise Refused(f"{where}: not an object")
        sid = slug(s.get("slug"), f"{where}.slug")
        if sid in seen:
            raise Refused(f"{where}: {sid} twice")
        seen.add(sid)
        name, host = s.get("name"), s.get("host")
        if not isinstance(name, str) or not NAME.match(name):
            raise Refused(f"{where}.name: not a plain name")
        if not isinstance(host, str) or not HOST.match(host):
            raise Refused(f"{where}.host: not a host name")
        services.append((sid, name, host))
        verdict = s.get("verdict")
        if verdict is None:
            continue
        if verdict not in VERDICTS:
            raise Refused(f"{where}.verdict: {verdict!r}")
        if checked_at is not None:
            snapshots.append(
                (
                    checked_at,
                    sid,
                    verdict,
                    *(count(s.get(k), f"{where}.{k}") for k in ("ok", "refused", "blocked", "dns", "counted")),
                )
            )

    hourly: dict[tuple[str, str], Row] = {}
    raw_history = doc.get("history", [])
    if not isinstance(raw_history, list):
        raise Refused("history: not a list")
    for i, h in enumerate(raw_history):
        where = f"history[{i}]"
        if not isinstance(h, dict) or not isinstance(h.get("verdicts"), dict):
            raise Refused(f"{where}: no verdicts")
        at = stamp(h.get("at"), f"{where}.at", now)
        for sid, verdict in h["verdicts"].items():
            slug(sid, f"{where}.verdicts")
            if verdict not in VERDICTS:
                raise Refused(f"{where}.verdicts.{sid}: {verdict!r}")
            hourly[(at, sid)] = (at, sid, verdict)
    for row in snapshots:
        hourly.setdefault((row[0], row[1]), row[:3])

    through: list[Row] = []
    t = doc.get("through_proxysvpn")
    if t is not None:
        if not isinstance(t, dict) or not isinstance(t.get("outcomes"), dict):
            raise Refused("through_proxysvpn: no outcomes")
        at = stamp(t.get("checked_at"), "through_proxysvpn.checked_at", now)
        exit_country = t.get("exit")
        if not isinstance(exit_country, str) or not COUNTRY.match(exit_country):
            raise Refused("through_proxysvpn.exit: not a country code")
        for sid, outcome in t["outcomes"].items():
            slug(sid, "through_proxysvpn.outcomes")
            if outcome not in OUTCOMES:
                raise Refused(f"through_proxysvpn.outcomes.{sid}: {outcome!r}")
            through.append((at, exit_country, sid, outcome))

    return Parsed(tuple(services), tuple(hourly.values()), tuple(snapshots), tuple(through), doc)


# ── Files ───────────────────────────────────────────────────────────────────


def read_rows(path: Path, fields: Sequence[str]) -> list[Row]:
    """Rows of a stored CSV. A file with another header is refused rather than overwritten."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return []
        if tuple(header) != tuple(fields):
            raise Refused(f"{path}: unexpected header {header}")
        rows = [tuple(r) for r in reader if r]
    for r in rows:
        if len(r) != len(fields):
            raise Refused(f"{path}: a row with {len(r)} cells")
    return rows


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_rows(path: Path, fields: Sequence[str], rows: Iterable[Row]) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(fields)
    writer.writerows(rows)
    write_atomic(path, buf.getvalue())


def merge_monthly(folder: Path, fields: Sequence[str], key_len: int, rows: Iterable[Row]) -> int:
    """Add rows that are not stored yet to ``folder/YYYY-MM.csv``. Returns how many were added.

    O(n log n) in the rows of the months touched.
    """
    by_month: dict[str, list[Row]] = {}
    for r in rows:
        by_month.setdefault(r[0][:7], []).append(r)
    added = 0
    for month, new_rows in by_month.items():
        path = folder / f"{month}.csv"
        stored = {r[:key_len]: r for r in read_rows(path, fields)}
        fresh = 0
        for r in new_rows:
            if r[:key_len] not in stored:
                stored[r[:key_len]] = r
                fresh += 1
        if fresh:
            write_rows(path, fields, sorted(stored.values()))
            added += fresh
    return added


def merge_services(path: Path, current: Sequence[Row]) -> list[Row]:
    """The service list in the site's order, then services the site no longer lists."""
    stored = read_rows(path, SERVICE_FIELDS)
    ids = {r[0] for r in current}
    merged = [*current, *(r for r in stored if r[0] not in ids)]
    if merged != stored:
        write_rows(path, SERVICE_FIELDS, merged)
    return merged


def _tally(
    folder: Path,
    fields: Sequence[str],
    service_at: int,
    value_at: int,
    keep: Callable[[Row], bool] = lambda _r: True,
) -> dict[tuple[str, str], Counter[str]]:
    tally: dict[tuple[str, str], Counter[str]] = {}
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            m = MONTH_FILE.match(path.name)
            if not m:
                continue
            for r in read_rows(path, fields):
                if keep(r):
                    tally.setdefault((m.group(1), r[service_at]), Counter())[r[value_at]] += 1
    return tally


def _summary_rows(tally: Mapping[tuple[str, str], Counter[str]], order_of: Sequence[str], services: Sequence[Row]) -> list[Row]:
    order = {r[0]: i for i, r in enumerate(services)}
    keys = sorted(tally, key=lambda k: (k[0], order.get(k[1], len(order)), k[1]))
    return [(month, sid, str(sum(tally[(month, sid)].values())), *(str(tally[(month, sid)][v]) for v in order_of)) for month, sid in keys]


def summarize(hourly_dir: Path, services: Sequence[Row]) -> list[Row]:
    """Per month and service: checks without a VPN and how many ended in each verdict. O(rows)."""
    return _summary_rows(_tally(hourly_dir, HOURLY_FIELDS, 1, 2), VERDICTS, services)


def is_home(row: Row) -> bool:
    """An hourly row measured from home networks (ISO UTC stamps compare as text)."""
    return row[0] >= HOME_SINCE


def summarize_home(hourly_dir: Path, services: Sequence[Row]) -> list[Row]:
    """summarize() over the rows measured from home networks only (is_home). O(rows)."""
    return _summary_rows(_tally(hourly_dir, HOURLY_FIELDS, 1, 2, is_home), VERDICTS, services)


def summarize_through(through_dir: Path, services: Sequence[Row]) -> list[Row]:
    """Per month and service: checks through ProxysVPN and how many ended in each outcome. O(rows)."""
    return _summary_rows(_tally(through_dir, THROUGH_FIELDS, 2, 3), OUTCOME_ORDER, services)


# ── README tables ───────────────────────────────────────────────────────────

# With ProxysVPN first, without a VPN beside it: a table of «did not open» alone
# reads as «nothing works», so each row shows both.
TABLES: Final[Mapping[str, Mapping[str, str]]] = {
    "en": {
        "title": "Month {month} (UTC): in what share of hourly checks each service opened. "
        "Checks without a VPN: {direct}; through ProxysVPN: {through}.",
        "service": "Service",
        "vpn": "With ProxysVPN",
        "direct": "Without VPN",
        "note": "«With ProxysVPN»: our own check from our server in Moscow through ProxysVPN. «Without VPN»: public "
        "Globalping points on Russian home networks; this table counts only checks since 4 November 2026, 01:00 UTC "
        "(earlier rows in `data/hourly` come from mostly data-centre points). Counts of every row: "
        "`data/summary.csv` and `data/summary-through-proxysvpn.csv`. Updated automatically.",
        "empty": "No data yet.",
        "works": "opened",
        "partial": "partly",
        "blocked": "did not open",
        "refused": "refused (403/451)",
        "unknown": "no data",
    },
    "ru": {
        "title": "Месяц {month} (UTC): в какой доле ежечасных проверок сервис открывался. "
        "Проверок без VPN: {direct}, через ProxysVPN: {through}.",
        "service": "Сервис",
        "vpn": "С ProxysVPN",
        "direct": "Без VPN",
        "note": "«С ProxysVPN» — наша проверка с нашего сервера в Москве через ProxysVPN. «Без VPN» — публичные точки "
        "Globalping на домашнем интернете в России; в таблице только проверки с 4 ноября 2026, 01:00 UTC "
        "(более ранние строки в `data/hourly` — в основном с серверов в дата-центрах). Счётчики всех строк: "
        "`data/summary.csv` и `data/summary-through-proxysvpn.csv`. Обновляется автоматически.",
        "empty": "Данных пока нет.",
        "works": "открывался",
        "partial": "частично",
        "blocked": "не открывался",
        "refused": "отказ (403/451)",
        "unknown": "нет данных",
    },
}
# A tick only when the service opened through ProxysVPN in nearly every check.
TICK_SHARE: Final[float] = 0.9


def percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "—"


def vpn_cell(words: Mapping[str, str], row: Row | None) -> str:
    if row is None or int(row[2]) == 0:
        return "—"
    checks, ok = int(row[2]), int(row[3])
    text = f"{words['works']} {percent(ok, checks)}"
    return f"✅ {text}" if ok / checks >= TICK_SHARE else text


def direct_cell(words: Mapping[str, str], row: Row | None) -> str:
    if row is None:
        return "—"
    checks = int(row[2])
    parts = [f"{words[v]} {percent(int(n), checks)}" for v, n in zip(VERDICTS, row[3:]) if int(n) > 0]
    return ", ".join(parts) if parts else "—"


def table(lang: str, summary: Sequence[Row], through: Sequence[Row], services: Sequence[Row]) -> str:
    """The README table of the latest month; `summary` is the home-network one (summarize_home)."""
    words = TABLES[lang]
    months = [r[0] for r in summary] + [r[0] for r in through]
    if not months:
        return words["empty"]
    month = max(months)
    names = {r[0]: r[1] for r in services}
    vpn = {r[1]: r for r in through if r[0] == month}
    direct = {r[1]: r for r in summary if r[0] == month}
    order = [r[0] for r in services]
    listed = set(order)
    ids = [sid for sid in order if sid in vpn or sid in direct] + sorted((set(vpn) | set(direct)) - listed)
    lines = [
        words["title"].format(
            month=month,
            direct=max((int(r[2]) for r in direct.values()), default=0),
            through=max((int(r[2]) for r in vpn.values()), default=0),
        ),
        "",
        f"| {words['service']} | {words['vpn']} | {words['direct']} |",
        "| --- | --- | --- |",
    ]
    for sid in ids:
        lines.append(f"| {names.get(sid, sid)} | {vpn_cell(words, vpn.get(sid))} | {direct_cell(words, direct.get(sid))} |")
    lines += ["", words["note"]]
    return "\n".join(lines)


def refresh_readme(path: Path, summary: Sequence[Row], through: Sequence[Row], services: Sequence[Row]) -> bool:
    """Replace the text between the summary markers. Returns True when the file changed."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new = text
    for lang in TABLES:
        start, end = f"<!-- summary:{lang}:start -->", f"<!-- summary:{lang}:end -->"
        a, b = new.find(start), new.find(end)
        if a == -1 or b == -1 or b < a:
            raise Refused(f"README.md: markers for {lang} missing")
        new = new[: a + len(start)] + "\n" + table(lang, summary, through, services) + "\n" + new[b:]
    if new != text:
        write_atomic(path, new)
        return True
    return False


# ── Main ────────────────────────────────────────────────────────────────────


def store(parsed: Parsed, root: Path) -> dict[str, int]:
    data = root / "data"
    services = merge_services(data / "services.csv", parsed.services)
    added = {
        "hourly": merge_monthly(data / "hourly", HOURLY_FIELDS, 2, parsed.hourly),
        "snapshots": merge_monthly(data / "snapshots", SNAPSHOT_FIELDS, 2, parsed.snapshots),
        "through": merge_monthly(data / "through-proxysvpn", THROUGH_FIELDS, 3, parsed.through),
    }
    summary = summarize(data / "hourly", services)
    if summary != read_rows(data / "summary.csv", SUMMARY_FIELDS):
        write_rows(data / "summary.csv", SUMMARY_FIELDS, summary)
    through = summarize_through(data / "through-proxysvpn", services)
    if through != read_rows(data / "summary-through-proxysvpn.csv", THROUGH_SUMMARY_FIELDS):
        write_rows(data / "summary-through-proxysvpn.csv", THROUGH_SUMMARY_FIELDS, through)
    refresh_readme(root / "README.md", summarize_home(data / "hourly", services), through, services)
    write_atomic(root / "latest.json", json.dumps(parsed.document, ensure_ascii=False, indent=2) + "\n")
    return added


def log(level: str, msg: str, **fields: Any) -> None:
    print(json.dumps({"level": level, "msg": msg, **fields}, ensure_ascii=False), file=sys.stderr if level == "error" else sys.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--source", default=SOURCE, help="URL, or a local file path for testing")
    args = parser.parse_args(argv)
    try:
        if args.source.startswith("https://"):
            raw = fetch(args.source)
        else:
            raw = Path(args.source).read_bytes()
        if raw is None:
            log("info", "the site has no run yet")
            return 0
        parsed = validate(raw, datetime.now(timezone.utc))
        added = store(parsed, args.root)
    except (Refused, OSError) as err:
        log("error", "refused", reason=str(err))
        return 1
    log("info", "stored", **added)
    return 0


if __name__ == "__main__":
    sys.exit(main())
