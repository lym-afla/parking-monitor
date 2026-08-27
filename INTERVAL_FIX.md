# Archived interval-fix note

This historical note is superseded. The monitor and command services now use
the shared cross-process lock in `state_store.py`, so an interval update is
merged with monitor counters instead of overwriting them.

Do not follow older instructions that made `/opt/parking_monitor` writable by a
service user. Current ownership, verification, update, and rollback procedures
are in [DEPLOYMENT.md](DEPLOYMENT.md).
