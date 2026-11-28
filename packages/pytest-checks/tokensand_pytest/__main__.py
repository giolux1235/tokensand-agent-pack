"""Select one private workflow test file for future pytest runs in this Git checkout."""
import argparse
from http.client import HTTPException
from importlib.metadata import PackageNotFoundError, version
import sqlite3
import sys

from . import connection, reporter


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m tokensand_pytest", description=__doc__)
    commands = parser.add_subparsers(required=True, dest="command")
    connect = commands.add_parser("connect", help="send only pass/fail counts when this file runs in full")
    connect.add_argument("project", help="private app ID from Tokens& Repository checks")
    connect.add_argument("test_file", help="trusted pytest file for this workflow")
    connect.add_argument("--queue-offline", action="store_true", help="keep undelivered counts in this checkout for up to seven days")
    commands.add_parser("sync", help="retry pending counts for this checkout")
    commands.add_parser("results", help="print the connected hosted app's private checks URL")
    commands.add_parser("disconnect", help="disconnect repository reporting and erase the checkout's pending counts")
    args = parser.parse_args(argv)
    links = []
    try:
        if args.command == "connect":
            try:
                supported = int(version("pytest").split(".", 1)[0]) >= 7
            except (PackageNotFoundError, ValueError):
                supported = False
            if not supported:
                print("Run this command in a Python environment with pytest 7 or later.", file=sys.stderr)
                return 1
            result = connection.connect(args.project, args.test_file, args.queue_offline)
        elif args.command == "results":
            url = reporter.results_url()
            if url:
                print(url)
                return 0
            print("No hosted Tokens& app is connected in this checkout.", file=sys.stderr)
            return 1
        else:
            show_link = args.command == "sync" and reporter.interactive_results(sys.stdout.isatty())
            result = reporter._work(args.command, on_delivered=links.append if show_link else None)
    except reporter.CertificateTrustError:
        print(reporter.TLS_ERROR, file=sys.stderr)
        return 1
    except (OSError, HTTPException, RuntimeError, sqlite3.Error, ValueError, KeyError):
        print("Check the test path, private app ID, repository reporting setting, endpoint and ingestion key. "
              "For a different app or test file, disconnect the previous checkout connection first.", file=sys.stderr)
        return 1
    print(result)
    if links:
        print("Private checks: " + links[0])
    return int(result not in {"connected", "delivered", "disconnected", "nothing pending"})


if __name__ == "__main__":
    sys.exit(main())
