#!/usr/bin/env python3
"""Audit and optionally clear macOS's Knowledge database (knowledgeC.db).

knowledgeC.db is written continuously by Apple's CoreDuet/Knowledge system
daemon. It records per-app foreground usage down to the session, Bluetooth
pairing events, notification metadata, and search/Siri activity, then
replicates a subset of it to other Apple devices signed into the same
iCloud account over the Rapport/Continuity sync protocol. It is protected
by TCC (Full Disk Access is required to open it even as the owning user),
so most people have never seen what it contains.

Subcommands:
  report   Read-only. Summarizes what has been logged: top apps, activity
           by hour/day, paired Bluetooth devices, notification senders,
           and which other devices this data has synced with.
  purge    Deletes existing rows. Does not stop future logging -- see the
           README for the System Settings toggles that reduce collection
           at the source.

The macOS Knowledge Core Data schema varies across OS releases; sections
that depend on a column or table your macOS version doesn't have are
skipped rather than failing.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / "Library/Application Support/Knowledge/knowledgeC.db"
# Core Data (NSDate) timestamps count seconds from 2001-01-01, not the Unix epoch.
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

logger = logging.getLogger("knowledgec_audit")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def core_data_timestamp(moment: datetime) -> float:
    return (moment - CORE_DATA_EPOCH).total_seconds()


def to_utc_datetime(core_data_value: float) -> datetime:
    return CORE_DATA_EPOCH + timedelta(seconds=core_data_value)


def connect(db_path: Path, read_only: bool) -> sqlite3.Connection:
    mode = "ro" if read_only else "rw"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode={mode}", uri=True, timeout=30)
        # sqlite3.connect() doesn't open the file until first use, so a TCC
        # denial only surfaces here, on this probe query.
        conn.execute("SELECT 1 FROM ZOBJECT LIMIT 1")
        return conn
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"Could not open {db_path} ({mode}): {exc}\n\n"
            "macOS restricts this file with Full Disk Access (TCC). Grant Full "
            "Disk Access to whatever process is running this script (Terminal, "
            "your editor, etc.) under System Settings > Privacy & Security > "
            "Full Disk Access, then fully quit and reopen that application."
        ) from exc


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    (found,) = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(found)


def columns_of(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@dataclass(frozen=True)
class StreamCount:
    stream_name: str
    row_count: int


def stream_inventory(conn: sqlite3.Connection) -> list[StreamCount]:
    """Every event-type path logged on this Mac, most frequent first."""
    rows = conn.execute(
        "SELECT ZSTREAMNAME, COUNT(*) FROM ZOBJECT GROUP BY ZSTREAMNAME ORDER BY 2 DESC"
    ).fetchall()
    return [StreamCount(name or "(unnamed)", count) for name, count in rows]


@dataclass(frozen=True)
class RetentionWindow:
    tracked_days: int
    earliest: datetime | None
    latest: datetime | None
    current_rows: int
    lifetime_rows: int


def retention_window(conn: sqlite3.Connection) -> RetentionWindow:
    """How much history is actually on disk right now vs. ever recorded."""
    earliest, latest = conn.execute(
        "SELECT MIN(ZSTARTDATE), MAX(ZENDDATE) FROM ZOBJECT "
        "WHERE ZSTREAMNAME = '/app/usage' AND ZSTARTDATE > 0"
    ).fetchone()
    (current_rows,) = conn.execute("SELECT COUNT(*) FROM ZOBJECT").fetchone()
    lifetime_rows = 0
    if table_exists(conn, "Z_PRIMARYKEY"):
        row = conn.execute(
            "SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = 'Object'"
        ).fetchone()
        lifetime_rows = row[0] if row and row[0] else 0
    earliest_dt = to_utc_datetime(earliest) if earliest else None
    latest_dt = to_utc_datetime(latest) if latest else None
    tracked_days = (
        (latest_dt.date() - earliest_dt.date()).days + 1
        if earliest_dt and latest_dt
        else 0
    )
    return RetentionWindow(
        tracked_days, earliest_dt, latest_dt, current_rows, lifetime_rows
    )


@dataclass(frozen=True)
class UsageEvent:
    bundle_id: str
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def fetch_app_usage(conn: sqlite3.Connection) -> list[UsageEvent]:
    """Per-app foreground sessions, localized to the timezone recorded at the time."""
    rows = conn.execute(
        "SELECT ZVALUESTRING, ZSTARTDATE, ZENDDATE, ZSECONDSFROMGMT FROM ZOBJECT "
        "WHERE ZSTREAMNAME = '/app/usage' AND ZSTARTDATE > 0 AND ZENDDATE >= ZSTARTDATE"
    ).fetchall()
    events = []
    for bundle_id, start, end, gmt_offset in rows:
        tz = timezone(timedelta(seconds=gmt_offset or 0))
        events.append(
            UsageEvent(
                bundle_id=bundle_id or "(unknown)",
                start=to_utc_datetime(start).astimezone(tz),
                end=to_utc_datetime(end).astimezone(tz),
            )
        )
    return events


def top_apps(events: Sequence[UsageEvent], limit: int) -> list[tuple[str, float, int]]:
    """(bundle_id, total_seconds, session_count), busiest first."""
    totals: defaultdict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    for event in events:
        totals[event.bundle_id] += event.duration_seconds
        counts[event.bundle_id] += 1
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return [
        (bundle_id, seconds, counts[bundle_id]) for bundle_id, seconds in ranked[:limit]
    ]


def usage_by_local_hour(events: Sequence[UsageEvent]) -> dict[int, float]:
    totals: defaultdict[int, float] = defaultdict(float)
    for event in events:
        totals[event.start.hour] += event.duration_seconds
    return dict(totals)


def usage_by_weekday(events: Sequence[UsageEvent]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for event in events:
        totals[event.start.strftime("%A")] += event.duration_seconds
    return dict(totals)


def busiest_days(
    events: Sequence[UsageEvent], limit: int = 5
) -> list[tuple[date, float, int]]:
    totals: defaultdict[date, float] = defaultdict(float)
    counts: Counter[date] = Counter()
    for event in events:
        day = event.start.date()
        totals[day] += event.duration_seconds
        counts[day] += 1
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return [(day, seconds, counts[day]) for day, seconds in ranked[:limit]]


@dataclass(frozen=True)
class BluetoothDevice:
    name: str
    connect_events: int
    first_seen: datetime
    last_seen: datetime


def fetch_bluetooth_devices(conn: sqlite3.Connection) -> list[BluetoothDevice]:
    if "Z_DKBLUETOOTHMETADATAKEY__NAME" not in columns_of(conn, "ZSTRUCTUREDMETADATA"):
        return []
    rows = conn.execute(
        "SELECT sm.Z_DKBLUETOOTHMETADATAKEY__NAME, COUNT(*), MIN(o.ZSTARTDATE), MAX(o.ZSTARTDATE) "
        "FROM ZOBJECT o JOIN ZSTRUCTUREDMETADATA sm ON o.ZSTRUCTUREDMETADATA = sm.Z_PK "
        "WHERE o.ZSTREAMNAME = '/bluetooth/isConnected' AND o.ZSTARTDATE > 0 "
        "GROUP BY sm.Z_DKBLUETOOTHMETADATAKEY__NAME ORDER BY 2 DESC"
    ).fetchall()
    return [
        BluetoothDevice(
            name or "(unnamed device)",
            count,
            to_utc_datetime(first),
            to_utc_datetime(last),
        )
        for name, count, first, last in rows
    ]


def fetch_notification_senders(conn: sqlite3.Connection) -> Counter[str]:
    if "Z_DKNOTIFICATIONUSAGEMETADATAKEY__BUNDLEID" not in columns_of(
        conn, "ZSTRUCTUREDMETADATA"
    ):
        return Counter()
    rows = conn.execute(
        "SELECT sm.Z_DKNOTIFICATIONUSAGEMETADATAKEY__BUNDLEID FROM ZOBJECT o "
        "JOIN ZSTRUCTUREDMETADATA sm ON o.ZSTRUCTUREDMETADATA = sm.Z_PK "
        "WHERE o.ZSTREAMNAME = '/notification/usage'"
    ).fetchall()
    return Counter(bundle_id or "(unknown)" for (bundle_id,) in rows)


@dataclass(frozen=True)
class SyncPeer:
    device_id: str
    model: str
    last_seen: datetime | None


def fetch_sync_peers(conn: sqlite3.Connection) -> list[SyncPeer]:
    """Other Apple devices this Knowledge data has replicated to via iCloud."""
    if not table_exists(conn, "ZSYNCPEER"):
        return []
    rows = conn.execute(
        "SELECT ZDEVICEID, ZMODEL, ZLASTSEENDATE FROM ZSYNCPEER "
        "WHERE ZDEVICEID IS NOT NULL AND ZDEVICEID != ''"
    ).fetchall()
    return [
        SyncPeer(
            device_id,
            model or "(unknown model)",
            to_utc_datetime(last_seen) if last_seen is not None else None,
        )
        for device_id, model, last_seen in rows
    ]


def format_duration(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)))


def print_report(conn: sqlite3.Connection, db_path: Path, top_n: int) -> None:
    print(f"knowledgeC.db report -- {db_path}")
    print("=" * 72)

    window = retention_window(conn)
    print(
        f"\nRetention: {window.tracked_days} day(s) of app-usage history on disk right now"
    )
    if window.earliest and window.latest:
        print(f"  {window.earliest:%Y-%m-%d} .. {window.latest:%Y-%m-%d}")
    if window.lifetime_rows:
        print(
            f"  {window.current_rows:,} rows present / {window.lifetime_rows:,} ever recorded "
            "(macOS deletes the rest on its own rolling schedule -- you were never asked)"
        )

    print("\nEvent types logged on this Mac:")
    print_table(
        ["stream", "rows"],
        [[s.stream_name, f"{s.row_count:,}"] for s in stream_inventory(conn)],
    )

    events = fetch_app_usage(conn)
    if events:
        total_seconds = sum(e.duration_seconds for e in events)
        active_days = len({e.start.date() for e in events})
        print(
            f"\nApp usage: {format_duration(total_seconds)} tracked across {active_days} day(s)"
        )

        print(f"\nTop {top_n} applications by tracked time:")
        print_table(
            ["bundle id", "time", "sessions"],
            [[b, format_duration(s), str(c)] for b, s, c in top_apps(events, top_n)],
        )

        hours = usage_by_local_hour(events)
        peak_hour = max(hours, key=hours.__getitem__)
        quiet_hours = [h for h in range(24) if hours.get(h, 0) == 0]
        print(
            f"\nBusiest hour of day (local time at the time of each event): "
            f"{peak_hour:02d}:00, {format_duration(hours[peak_hour])} total"
        )
        if quiet_hours:
            print(
                f"No activity ever recorded during: {', '.join(f'{h:02d}:00' for h in quiet_hours)}"
            )

        weekdays = usage_by_weekday(events)
        busiest_weekday = max(weekdays, key=weekdays.__getitem__)
        print(
            f"Heaviest day of the week: {busiest_weekday} ({format_duration(weekdays[busiest_weekday])} total)"
        )

        print("\nBusiest individual days:")
        print_table(
            ["date", "time", "sessions"],
            [
                [f"{d:%Y-%m-%d}", format_duration(s), str(c)]
                for d, s, c in busiest_days(events)
            ],
        )

        longest = max(events, key=lambda e: e.duration_seconds)
        print(
            f"\nLongest single session: {longest.bundle_id}, {format_duration(longest.duration_seconds)}, "
            f"started {longest.start:%Y-%m-%d %H:%M}"
        )

    bt_devices = fetch_bluetooth_devices(conn)
    if bt_devices:
        print("\nBluetooth devices this Mac has connected to:")
        print_table(
            ["device", "connect events", "first seen", "last seen"],
            [
                [
                    d.name,
                    str(d.connect_events),
                    f"{d.first_seen:%Y-%m-%d}",
                    f"{d.last_seen:%Y-%m-%d}",
                ]
                for d in bt_devices
            ],
        )

    senders = fetch_notification_senders(conn)
    if senders:
        print("\nNotification senders logged:")
        print_table(
            ["bundle id", "count"], [[b, str(c)] for b, c in senders.most_common()]
        )

    peers = fetch_sync_peers(conn)
    if peers:
        print(
            f"\nThis data has synced with {len(peers)} other Apple device(s) via iCloud/Continuity:"
        )
        print_table(
            ["device id", "model", "last seen"],
            [
                [
                    p.device_id,
                    p.model,
                    f"{p.last_seen:%Y-%m-%d}" if p.last_seen else "unknown",
                ]
                for p in peers
            ],
        )
        print("  -> this Mac's activity history is not confined to this Mac.")


def purge(
    conn: sqlite3.Connection, db_path: Path, keep_days: int, assume_yes: bool
) -> None:
    cutoff = core_data_timestamp(datetime.now(timezone.utc) - timedelta(days=keep_days))

    (to_delete,) = conn.execute(
        "SELECT COUNT(*) FROM ZOBJECT WHERE ZSTARTDATE > 0 AND ZSTARTDATE < ?",
        (cutoff,),
    ).fetchone()
    (total,) = conn.execute("SELECT COUNT(*) FROM ZOBJECT").fetchone()
    logger.info("%d of %d rows are older than %d day(s)", to_delete, total, keep_days)

    if to_delete == 0:
        logger.info("nothing to delete")
        return

    if not assume_yes:
        reply = input(f"Delete {to_delete} rows from {db_path}? [y/N] ")
        if reply.strip().lower() != "y":
            logger.info("aborted, no changes made")
            return

    with conn:
        # Metadata must be deleted before ZOBJECT: the subquery looks up each
        # row's ZSTRUCTUREDMETADATA foreign key from ZOBJECT itself, so
        # reversing the order would orphan the metadata rows instead.
        conn.execute(
            "DELETE FROM ZSTRUCTUREDMETADATA WHERE Z_PK IN ("
            "  SELECT ZSTRUCTUREDMETADATA FROM ZOBJECT"
            "  WHERE ZSTARTDATE > 0 AND ZSTARTDATE < ? AND ZSTRUCTUREDMETADATA IS NOT NULL"
            ")",
            (cutoff,),
        )
        conn.execute(
            "DELETE FROM ZOBJECT WHERE ZSTARTDATE > 0 AND ZSTARTDATE < ?", (cutoff,)
        )

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")

    (remaining,) = conn.execute("SELECT COUNT(*) FROM ZOBJECT").fetchone()
    logger.info(
        "deleted %d rows (%d -> %d remaining)", total - remaining, total, remaining
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="knowledgec_audit",
        description="Inspect and optionally clear macOS's knowledgeC.db activity log.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to knowledgeC.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser(
        "report", help="Read-only summary of what's logged."
    )
    report_parser.add_argument(
        "--top", type=int, default=15, help="Number of top apps to show."
    )

    purge_parser = subparsers.add_parser("purge", help="Delete existing history.")
    purge_parser.add_argument(
        "--keep-days",
        type=int,
        default=0,
        help="Delete rows older than this many days (default: 0, deletes everything).",
    )
    purge_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt."
    )

    args = parser.parse_args()
    configure_logging(args.verbose)

    if not args.db_path.exists():
        raise SystemExit(
            f"{args.db_path} does not exist. Either Knowledge logging isn't active on "
            "this Mac, or your macOS version stores it elsewhere -- check with:\n"
            "  find ~/Library -iname 'knowledgeC.db' 2>/dev/null"
        )

    read_only = args.command == "report"
    with closing(connect(args.db_path, read_only=read_only)) as conn:
        if args.command == "report":
            print_report(conn, args.db_path, args.top)
        elif args.command == "purge":
            try:
                purge(conn, args.db_path, args.keep_days, args.yes)
            except sqlite3.OperationalError as exc:
                raise SystemExit(
                    f"write failed: {exc}. The 'knowledgeC' daemon may be holding a "
                    "lock on the file -- wait a moment and retry."
                ) from exc


if __name__ == "__main__":
    main()
