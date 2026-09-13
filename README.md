# knowledgec-privacy-audit

See what macOS's hidden "Knowledge" database has logged about you, and clear it.

Maintained by jsdev.

## What this is

Every Mac running a modern version of macOS keeps a file at
`~/Library/Application Support/Knowledge/knowledgeC.db`. It's written
continuously by an Apple system daemon (part of the CoreDuet / "Knowledge"
framework) and it logs, at minimum:

- Every app you bring to the foreground, with start and end timestamps down
  to the session (not just "used Chrome today" -- individual multi-second
  bursts, thousands of them a month).
- Every Bluetooth device you connect to, by name, with a timestamp for each
  connection. On a laptop that pairs with a car's hands-free system, this is
  a timestamped log of when you got in the car.
- Notification delivery events.
- Search/Siri/Spotlight activity signals.
- A sync table (`ZSYNCPEER`) that replicates a subset of this data to your
  other Apple devices signed into the same iCloud account, via the same
  Rapport/Continuity protocol Handoff uses. Running this tool on one Mac
  can surface device identifiers and models for iPhones and iPads you own
  that have nothing to do with that Mac.

You cannot read this file with a normal app. macOS gates it behind Full Disk
Access (a TCC permission), which is presumably an acknowledgment by Apple
that the contents are sensitive. Most Mac users have never seen it and don't
know it exists. It is also a well-documented target in mobile/desktop
forensics tooling (Sarah Edwards' APOLLO project, and commercial extraction
suites like Cellebrite and Magnet AXIOM, parse this exact file specifically
because of how much "pattern of life" data it contains) -- so the practical
exposure isn't hypothetical, it's an established forensic artifact.

This project doesn't claim Apple transmits the raw file off your devices; I
have no evidence either way on that. What's demonstrable, and what this tool
shows directly from your own database, is that the data exists in far more
detail than most people expect, that it already replicates across your own
devices without a prompt, and that anyone who gets Full Disk Access to one
of your Macs -- physically, via remote management, or via forensic tooling
-- gets a timestamped activity log most people believe doesn't exist.

## What's in the box

`knowledgec_audit.py` -- a single dependency-free Python script with two
subcommands:

```
knowledgec_audit.py report              # read-only: show what's logged
knowledgec_audit.py purge [--keep-days N]  # delete existing history
```

No third-party packages, nothing phones home, nothing writes anywhere except
the database file itself when you explicitly run `purge`. Read the script;
it's about 350 lines and every query in it is named for what it does.

## Requirements

- macOS (this file doesn't exist on other platforms).
- Python 3.10 or newer (`python3 --version`; macOS ships one).
- Full Disk Access granted to whatever will run the script.

## Granting Full Disk Access

macOS will refuse to open the database ("authorization denied") until you do
this, even though it's your own file and your own user account:

1. Open System Settings -> Privacy & Security -> Full Disk Access.
2. Click `+` and add the application you'll run this from (Terminal.app,
   iTerm, VS Code, etc.).
3. Turn the toggle on.
4. Fully quit (Cmd+Q) and reopen that application. A window restart is not
   enough -- TCC checks at process launch.

## Usage

```bash
python3 knowledgec_audit.py report
python3 knowledgec_audit.py report --top 25
python3 knowledgec_audit.py purge --keep-days 0        # asks for confirmation
python3 knowledgec_audit.py purge --keep-days 7 --yes  # keep last week, no prompt
python3 knowledgec_audit.py --db-path /path/to/copy.db report   # test against a copy
```

`report` never writes to the database (it opens it `mode=ro`). `purge` opens
it read-write and asks for a `y` confirmation before deleting anything unless
you pass `--yes`.

### Example output shape

Numbers below are illustrative, not real data from any machine:

```
Retention: 23 day(s) of app-usage history on disk right now
  2026-08-12 .. 2026-09-13
  10,426 rows present / 217,561 ever recorded (macOS deletes the rest on its
  own rolling schedule -- you were never asked)

Top 5 applications by tracked time:
bundle id              time     sessions
---------------------  -------  --------
com.google.Chrome      118h54m  3703
com.microsoft.VSCode   55h25m   3344
...

Bluetooth devices this Mac has connected to:
device               connect events  first seen  last seen
-------------------  --------------  ----------  ----------
<car head unit>       223            2026-08-14  2026-09-12

This data has synced with 2 other Apple device(s) via iCloud/Continuity:
device id                              model        last seen
--------------------------------------  -----------  ----------
DD096144-...                            iPhone14,4   2026-08-20
A7BC5F08-...                            iPad5,1      2026-08-20
```

## Important limitations

- **Purging does not stop future logging.** The daemon that owns this file
  restarts logging within seconds of a purge. To reduce what gets collected
  going forward, use:
  - System Settings -> Siri & Spotlight -> turn off "Learn from this Mac".
  - System Settings -> Screen Time -> turn it off.
  - Neither of these fully stops the lower-level Bluetooth/notification
    streams; there is no user-facing toggle for those as of this writing.
- **The schema varies by macOS version.** `ZSTRUCTUREDMETADATA` has roughly
  190 sparse columns across every Apple subsystem that has ever logged into
  Knowledge; this tool checks for the columns/tables it needs and silently
  skips a section if your macOS version doesn't have it, rather than
  crashing.
- **This does not cover iOS/iPadOS.** Those devices sandbox this file
  differently; this tool only reads a local macOS copy.
- **Be careful sharing `report` output.** It contains real device names
  (your car's Bluetooth name, your headphones), device identifiers for your
  other Apple hardware, and enough timing detail to reconstruct your daily
  schedule. Redact before pasting it anywhere public.

## Why publish this

I found this by accident while poking at my own Mac and was surprised by how
much detail this file holds and that it's already syncing across devices I
own without any indication it was happening. Publishing the tool that found
it seemed more useful than a blog post: run it on your own machine, look at
your own data, decide for yourself whether you're comfortable with it.

## Contributing

Pull requests that add support for additional `ZSTREAMNAME` values or
`ZSTRUCTUREDMETADATA` columns seen on other macOS versions are especially
welcome -- open an issue with the output of:

```bash
sqlite3 ~/Library/Application\ Support/Knowledge/knowledgeC.db \
  "SELECT ZSTREAMNAME, COUNT(*) FROM ZOBJECT GROUP BY ZSTREAMNAME;"
```

## License

MIT. See [LICENSE](LICENSE).
