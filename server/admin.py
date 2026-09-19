"""admin.py: moderation of the upload clients, run on the host next to ingest.py.

    ssh rimhaven python3 /opt/osmsfm/admin.py list
    ssh rimhaven python3 /opt/osmsfm/admin.py ban anon-3f9c2a --purge     # stop it and delete its rows
    ssh rimhaven python3 /opt/osmsfm/admin.py ban anon-3f9c2a --ip        # also its address and every key from it
    ssh rimhaven python3 /opt/osmsfm/admin.py unban anon-3f9c2a [--ip]
    ssh rimhaven python3 /opt/osmsfm/admin.py purge some-old-label        # rows only; works for labels without a key too

A ban takes effect on the client's next upload, the running service reads it from the database.
After a purge `data.json` is rewritten here, the service does not need a restart.

Run as root it first becomes the owner of the database (www-data): a root-owned SQLite journal
or data.json would lock the service out of its own files.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DB = Path(os.environ.get("OSMSFM_DB", "/var/lib/osmsfm/prices.sqlite"))


def _drop_privileges() -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0 or not DB.exists():
        return
    st = DB.stat()
    os.setgroups([])
    os.setgid(st.st_gid)
    os.setuid(st.st_uid)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="clients with their row counts")
    for name in ("ban", "unban"):
        p = sub.add_parser(name)
        p.add_argument("label")
        p.add_argument("--ip", action="store_true", help="also the address the key was registered from")
        if name == "ban":
            p.add_argument("--purge", action="store_true", help="also delete the rows it uploaded")
    p = sub.add_parser("purge", help="delete every row uploaded under a label")
    p.add_argument("label")
    args = ap.parse_args()

    _drop_privileges()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ingest  # same store, paths and export as the service; importing does not start the server

    clients = ingest.clients
    if args.cmd == "list":
        rows = clients.listing()
        print(f"{'label':26} {'rows':>7}  {'registered':19}  {'last upload':19}  {'address':16}  state")
        for r in rows:
            state = "BANNED" if r["banned"] else ("" if r["key"] else "no key (shared token)")
            if r["ip_banned"]:
                state += " +address"
            print(f"{(r['label'] or '?'):26} {r['rows']:>7}  {r['created'] or '':19}  {r['last_seen'] or '':19}  "
                  f"{r['ip_hash'] or '':16}  {state}")
        return 0
    if args.cmd in ("ban", "unban"):
        if not clients.set_banned(args.label, args.cmd == "ban", with_ip=args.ip):
            print(f"no client with the label {args.label!r} (labels without a key cannot be banned, only purged)")
            return 1
        print(f"{args.label}: {'banned' if args.cmd == 'ban' else 'unbanned'}" + (" with its address" if args.ip else ""))
    if args.cmd == "purge" or getattr(args, "purge", False):
        n = clients.purge(args.label, ingest.store.crops)
        ingest.export()
        print(f"{args.label}: {n} rows deleted, data.json rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
