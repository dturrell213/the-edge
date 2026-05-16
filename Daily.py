#!/usr/bin/env python3
"""
The Daily Degenerate — Daily Runner
Run this every morning to generate picks, log them, and push to GitHub.

Usage:
  python daily.py           # run today's picks
  python daily.py --settle  # settle yesterday's results (run at night)
  python daily.py --full    # run picks + settle yesterday + push everything
"""

import os, sys, subprocess, argparse
from datetime import datetime
from pathlib import Path

def run(cmd, desc):
    print(f"\n  {desc}...")
    result = subprocess.run(cmd, shell=True, capture_output=False)
    return result.returncode == 0

def git_push(message):
    print("\n  Pushing to GitHub...")
    os.system("git add exports/latest.json")
    os.system("git add exports/results.json")
    os.system(f'git commit -m "{message}"')
    result = os.system("git push origin master")
    if result == 0:
        print("  ✓ Dashboard updated on GitHub")
    else:
        print("  ✗ Push failed — check your internet connection")

def morning_run():
    print(f"\n{'='*55}")
    print(f"  THE DAILY DEGENERATE — MORNING RUN")
    print(f"  {datetime.now().strftime('%A, %B %d %Y — %I:%M %p')}")
    print(f"{'='*55}")

    # Step 1 — Run the model
    success = run(
        "python main.py --sport mlb --today --export",
        "Step 1/3 — Running value model"
    )
    if not success:
        print("\n  ✗ Model failed. Check main.py for errors.")
        return

    # Step 2 — Log picks to tracker
    run(
        "python tracker.py --log",
        "Step 2/3 — Logging picks to tracker"
    )

    # Step 3 — Copy latest export to latest.json
    run(
        "python -c \""
        "import json, os, pathlib; "
        "exports = sorted([f for f in os.listdir('exports') if f.endswith('.json') and f not in ['latest.json','results.json']]); "
        "data = json.load(open('exports/' + exports[-1])); "
        "json.dump(data, open('exports/latest.json','w'), indent=2); "
        "print('  Latest export saved as exports/latest.json')\"",
        "Step 3/3 — Saving latest.json for dashboard"
    )

    # Step 4 — Push to GitHub
    today = datetime.now().strftime("%Y-%m-%d")
    git_push(f"Daily picks {today}")

    print(f"\n{'='*55}")
    print(f"  ✓ Morning run complete!")
    print(f"  Dashboard: https://dturrell213.github.io/the-edge/")
    print(f"{'='*55}\n")

def nightly_settle():
    print(f"\n{'='*55}")
    print(f"  THE DAILY DEGENERATE — NIGHTLY SETTLE")
    print(f"  {datetime.now().strftime('%A, %B %d %Y — %I:%M %p')}")
    print(f"{'='*55}")

    # Step 1 — Settle results
    run(
        "python tracker.py --settle",
        "Step 1/3 — Checking MLB scores"
    )

    # Step 2 — Push results to dashboard
    run(
        "python tracker.py --push",
        "Step 2/3 — Updating dashboard results"
    )

    # Step 3 — Show summary
    run(
        "python tracker.py --summary",
        "Step 3/3 — Printing record"
    )

    # Step 4 — Push to GitHub
    today = datetime.now().strftime("%Y-%m-%d")
    git_push(f"Settle results {today}")

    print(f"\n{'='*55}")
    print(f"  ✓ Nightly settle complete!")
    print(f"  Dashboard: https://dturrell213.github.io/the-edge/")
    print(f"{'='*55}\n")

def full_run():
    morning_run()
    nightly_settle()

def main():
    parser = argparse.ArgumentParser(description="The Daily Degenerate — Daily Runner")
    parser.add_argument("--settle", action="store_true", help="Settle yesterday's results")
    parser.add_argument("--full",   action="store_true", help="Run picks + settle + push")
    args = parser.parse_args()

    if args.full:
        full_run()
    elif args.settle:
        nightly_settle()
    else:
        morning_run()

if __name__ == "__main__":
    main()
