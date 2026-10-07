#!/usr/bin/env python3
"""Check bundled source integrity, without executing it or making requests."""
from setup_server import verify_snapshot

if __name__ == "__main__":
    lock = verify_snapshot()
    print(f"Server snapshot integrity verified: {lock['snapshot_date']}")
