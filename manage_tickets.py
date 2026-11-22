"""Staff tool for support tickets, from the terminal.

  python manage_tickets.py list                      all tickets
  python manage_tickets.py list Pending              only Pending (or "In progress", Done)
  python manage_tickets.py set TKT-1001 Done "bag returned to customer"
  python manage_tickets.py history TKT-1001          every status change
"""
from __future__ import annotations

import sys

from support.ticket_manager import TicketError, TicketManager


def main(args: list[str], tm: TicketManager | None = None) -> None:
    tm = tm or TicketManager()
    cmd = args[0] if args else "list"
    if cmd == "list":
        rows = tm.list_tickets(args[1] if len(args) > 1 else None)
        print(f"{len(rows)} ticket(s)")
        for t in rows:
            print(f"{t['ticket_id']}  {t['status']:<11}  {t['priority']:<6}  {t['updated_at']}  {t['category']}")
    elif cmd == "set" and len(args) >= 3:
        try:
            row = tm.update_status(args[1], args[2], " ".join(args[3:]), changed_by="staff (terminal)")
            print(f"{row['ticket_id']} is now {row['status']}")
        except TicketError as exc:
            print(f"Error: {exc}")
    elif cmd == "history" and len(args) >= 2:
        for h in tm.history(args[1]):
            print(f"{h['at']}  {h['status']:<11}  {h['changed_by']:<18}  {h['note']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
