#!/usr/bin/env python3
"""
The Daily Degenerate — Results Tracker
Automatically logs picks and checks MLB scores to mark wins/losses.

Usage:
  python tracker.py --log        # log today's picks from latest export
  python tracker.py --settle     # check scores and settle pending picks
  python tracker.py --summary    # print running record and ROI
  python tracker.py --push       # update exports/results.json for dashboard
  python tracker.py --all        # log + settle + push (run this nightly)
"""

import os, json, argparse, requests
from datetime import datetime, date, timedelta
from pathlib import Path

TRACKER_FILE = Path("results/tracker.json")
RESULTS_FILE = Path("exports/results.json")
EXPORTS_DIR  = Path("exports")
MLB_BASE     = "https://statsapi.mlb.com/api/v1"

# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup():
    Path("results").mkdir(exist_ok=True)
    EXPORTS_DIR.mkdir(exist_ok=True)
    if not TRACKER_FILE.exists():
        save_tracker({"picks": [], "created": datetime.now().isoformat(), "version": "1.0"})
        print("  Created new tracker at results/tracker.json")

def load_tracker():
    with open(TRACKER_FILE) as f:
        return json.load(f)

def save_tracker(data):
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─────────────────────────────────────────────────────────────────────────────
# LOG TODAY'S PICKS
# ─────────────────────────────────────────────────────────────────────────────

def log_picks():
    setup()
    data = load_tracker()

    # Find latest export
    exports = sorted([
        f for f in EXPORTS_DIR.iterdir()
        if f.suffix == ".json"
        and f.name != "latest.json"
        and f.name != "results.json"
        and "baseball" in f.name
    ])

    if not exports:
        print("  No export files found. Run main.py --export first.")
        return

    latest = exports[-1]
    print(f"\n  Loading picks from: {latest.name}")

    with open(latest) as f:
        export = json.load(f)

    bets = export.get("value_bets", [])
    if not bets:
        print("  No value bets found in export.")
        return

    # Sort by edge, cap at 5
    bets = [b for b in bets if abs(b.get("best_odds_american", 0)) <= 350]
    bets = sorted(bets, key=lambda x: x.get("edge_pp", 0), reverse=True)[:5]

    today = date.today().isoformat()
    logged = 0

    for bet in bets:
        # Build unique ID
        pick_id = f"{today}_{bet.get('game','')}_{bet.get('team','')}"

        # Skip if already logged
        if any(p.get("pick_id") == pick_id for p in data["picks"]):
            print(f"  Already logged: {bet.get('team')} — skipping")
            continue

        # Parse game teams
        game_parts = bet.get("game", "").split(" vs ")
        away_team = game_parts[0].strip() if len(game_parts) > 0 else ""
        home_team = game_parts[1].strip() if len(game_parts) > 1 else ""

        pick = {
            "pick_id":       pick_id,
            "date":          today,
            "logged_at":     datetime.now().isoformat(),
            "result":        "P",   # P=pending, W=win, L=loss, V=void
            "settled_at":    None,
            # Core pick
            "sport":         bet.get("sport", "baseball_mlb"),
            "game":          bet.get("game", ""),
            "home_team":     home_team,
            "away_team":     away_team,
            "time":          bet.get("time", ""),
            "team":          bet.get("team", ""),
            "best_bookmaker":bet.get("best_bookmaker", ""),
            "best_odds":     bet.get("best_odds", ""),
            "best_odds_american": bet.get("best_odds_american", 0),
            # Model data
            "book_implied_pct": bet.get("best_book_implied_pct", 0),
            "market_avg_pct":   bet.get("market_avg_prob_pct", 0),
            "model_prob_pct":   bet.get("model_prob_pct", 0),
            "edge_pp":          bet.get("edge_pp", 0),
            # Pitcher matchup
            "home_pitcher":  bet.get("home_pitcher", ""),
            "away_pitcher":  bet.get("away_pitcher", ""),
            # Team data
            "home_wins":     bet.get("home_wins", 0),
            "home_losses":   bet.get("home_losses", 0),
            "away_wins":     bet.get("away_wins", 0),
            "away_losses":   bet.get("away_losses", 0),
            "home_form_l10": bet.get("home_form_l10", ""),
            "away_form_l10": bet.get("away_form_l10", ""),
            "home_streak":   bet.get("home_streak", ""),
            "away_streak":   bet.get("away_streak", ""),
            "home_run_diff": bet.get("home_run_diff", 0),
            "home_roster_wrc": bet.get("home_roster_wrc", 0),
            # Context
            "venue":         bet.get("venue", ""),
            "park_factor":   bet.get("park_factor", 1.0),
            "weather_temp_f":      bet.get("weather_temp_f", ""),
            "weather_wind_mph":    bet.get("weather_wind_mph", ""),
            "weather_precip_pct":  bet.get("weather_precip_pct", ""),
            # Settlement
            "final_home_score": None,
            "final_away_score": None,
            "actual_winner":    None,
            # Profit/loss (assumes $110 to win $100 at -110)
            "units_wagered": 1.0,
            "units_profit":  None,
        }

        data["picks"].append(pick)
        logged += 1
        odds = bet.get("best_odds", "")
        print(f"  ✓ Logged: {bet.get('team')} {odds} | Edge: +{bet.get('edge_pp',0):.1f}pp")

    save_tracker(data)
    print(f"\n  Logged {logged} new pick(s). Total tracked: {len(data['picks'])}\n")

# ─────────────────────────────────────────────────────────────────────────────
# SETTLE RESULTS (check MLB scores)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_mlb_scores(game_date):
    """Fetch final scores from MLB Stats API for a given date."""
    try:
        resp = requests.get(f"{MLB_BASE}/schedule", params={
            "sportId": 1,
            "date": game_date,
            "hydrate": "linescore,team",
        }, timeout=15)
        if resp.status_code != 200:
            return {}

        scores = {}
        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                status = game.get("status", {}).get("abstractGameState", "")
                if status != "Final":
                    continue

                home = game["teams"]["home"]
                away = game["teams"]["away"]
                home_name  = home["team"]["name"]
                away_name  = away["team"]["name"]
                home_score = home.get("score", 0)
                away_score = away.get("score", 0)

                winner = home_name if home_score > away_score else away_name

                scores[home_name] = {
                    "home_score": home_score,
                    "away_score": away_score,
                    "home_team":  home_name,
                    "away_team":  away_name,
                    "winner":     winner,
                }
                scores[away_name] = scores[home_name]

        return scores
    except Exception as e:
        print(f"  Warning: score fetch error: {e}")
        return {}

def calculate_profit(odds_american, result):
    """Calculate profit/loss in units. 1 unit = $110 risked."""
    if result not in ["W", "L"]:
        return None
    odds = int(odds_american)
    if result == "W":
        if odds > 0:
            return round(odds / 100, 3)
        else:
            return round(100 / abs(odds), 3)
    else:
        return -1.0

def settle_picks():
    setup()
    data = load_tracker()

    pending = [p for p in data["picks"] if p.get("result") == "P"]
    if not pending:
        print("\n  No pending picks to settle.\n")
        return

    print(f"\n  Settling {len(pending)} pending pick(s)...\n")

    # Group by date
    by_date = {}
    for pick in pending:
        d = pick.get("date", date.today().isoformat())
        by_date.setdefault(d, []).append(pick)

    settled = 0

    for game_date, picks in by_date.items():
        print(f"  Checking scores for {game_date}...")
        scores = fetch_mlb_scores(game_date)

        if not scores:
            print(f"  No final scores found for {game_date} — games may still be in progress.")
            continue

        for pick in picks:
            team = pick.get("team", "")
            home = pick.get("home_team", "")
            away = pick.get("away_team", "")

            # Find score entry
            score_entry = scores.get(home) or scores.get(away)
            if not score_entry:
                # Try fuzzy match
                for score_team, entry in scores.items():
                    if any(w in score_team for w in team.split() if len(w) > 3):
                        score_entry = entry
                        break

            if not score_entry:
                print(f"  ⚠ Could not find score for: {pick.get('game','')}")
                continue

            winner = score_entry.get("winner", "")
            home_score = score_entry.get("home_score", 0)
            away_score = score_entry.get("away_score", 0)

            # Determine result
            # Check if our pick team matches winner
            pick_team = team.lower()
            winner_lower = winner.lower()

            # Fuzzy match — check if any word from pick team is in winner
            pick_words = [w for w in pick_team.split() if len(w) > 3]
            is_winner = any(w in winner_lower for w in pick_words)

            result = "W" if is_winner else "L"
            profit = calculate_profit(pick.get("best_odds_american", -110), result)

            # Update pick in tracker
            for p in data["picks"]:
                if p.get("pick_id") == pick.get("pick_id"):
                    p["result"]           = result
                    p["settled_at"]       = datetime.now().isoformat()
                    p["final_home_score"] = home_score
                    p["final_away_score"] = away_score
                    p["actual_winner"]    = winner
                    p["units_profit"]     = profit
                    break

            icon = "✓ WIN" if result == "W" else "✗ LOSS"
            print(f"  {icon}: {team} | Score: {away} {away_score} @ {home} {home_score}")
            settled += 1

    save_tracker(data)
    print(f"\n  Settled {settled} pick(s).\n")

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY REPORT
# ─────────────────────────────────────────────────────────────────────────────

def print_summary():
    setup()
    data = load_tracker()
    picks = data.get("picks", [])

    if not picks:
        print("\n  No picks tracked yet.\n")
        return

    wins    = [p for p in picks if p.get("result") == "W"]
    losses  = [p for p in picks if p.get("result") == "L"]
    pending = [p for p in picks if p.get("result") == "P"]

    total_settled = len(wins) + len(losses)
    win_rate = len(wins) / total_settled if total_settled > 0 else 0

    # ROI
    total_profit = sum(p.get("units_profit", 0) or 0 for p in picks if p.get("units_profit") is not None)
    total_risked = total_settled * 1.0
    roi = (total_profit / total_risked * 100) if total_risked > 0 else 0

    # By edge bucket
    buckets = {}
    for p in picks:
        if p.get("result") not in ["W","L"]: continue
        edge = p.get("edge_pp", 0)
        if edge < 10:   bucket = "3-10% edge"
        elif edge < 15: bucket = "10-15% edge"
        else:           bucket = "15%+ edge"
        buckets.setdefault(bucket, {"W":0,"L":0})
        buckets[bucket][p["result"]] += 1

    print(f"\n{'='*55}")
    print(f"  THE DAILY DEGENERATE — RESULTS TRACKER")
    print(f"{'='*55}\n")
    print(f"  OVERALL RECORD")
    print(f"  {'─'*40}")
    print(f"  Record:     {len(wins)}W - {len(losses)}L ({len(pending)} pending)")
    print(f"  Win Rate:   {win_rate*100:.1f}%  (breakeven: 52.4%)")
    print(f"  Units P/L:  {total_profit:+.2f} units")
    print(f"  Est. ROI:   {roi:+.1f}%")

    if buckets:
        print(f"\n  BY EDGE SIZE")
        print(f"  {'─'*40}")
        for bucket in ["3-10% edge","10-15% edge","15%+ edge"]:
            b = buckets.get(bucket)
            if b:
                t = b["W"] + b["L"]
                wr = b["W"]/t*100 if t > 0 else 0
                print(f"  {bucket}: {b['W']}-{b['L']} ({wr:.1f}%)")

    print(f"\n  RECENT PICKS")
    print(f"  {'─'*40}")
    recent = sorted(picks, key=lambda x: x.get("date",""), reverse=True)[:10]
    for p in recent:
        result = p.get("result","P")
        icon = "✓" if result=="W" else "✗" if result=="L" else "⏳"
        odds = p.get("best_odds","")
        edge = p.get("edge_pp",0)
        print(f"  {icon} {p.get('date','')} | {p.get('team','')} {odds} | Edge: +{edge:.1f}pp | {result}")

    print(f"\n{'='*55}\n")

# ─────────────────────────────────────────────────────────────────────────────
# PUSH TO DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

def push_results():
    """Write results.json for the dashboard to read."""
    setup()
    data = load_tracker()
    picks = data.get("picks", [])

    wins   = [p for p in picks if p.get("result") == "W"]
    losses = [p for p in picks if p.get("result") == "L"]
    pending = [p for p in picks if p.get("result") == "P"]

    total_settled = len(wins) + len(losses)
    win_rate = len(wins) / total_settled if total_settled > 0 else 0
    total_profit = sum(p.get("units_profit",0) or 0 for p in picks if p.get("units_profit") is not None)
    roi = (total_profit / total_settled * 100) if total_settled > 0 else 0

    # Yesterday's picks for dashboard
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_picks = [p for p in picks if p.get("date") == yesterday]

    # All settled picks for history
    history = sorted(
        [p for p in picks if p.get("result") in ["W","L"]],
        key=lambda x: x.get("date",""),
        reverse=True
    )[:30]  # last 30 settled picks

    output = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_picks":    len(picks),
            "wins":           len(wins),
            "losses":         len(losses),
            "pending":        len(pending),
            "win_rate_pct":   round(win_rate * 100, 1),
            "units_profit":   round(total_profit, 2),
            "roi_pct":        round(roi, 1),
            "record":         f"{len(wins)}-{len(losses)}",
        },
        "results": [
            {
                "date":         p.get("date",""),
                "game":         p.get("game",""),
                "team":         p.get("team",""),
                "best_odds":    p.get("best_odds",""),
                "best_odds_american": p.get("best_odds_american",0),
                "edge_pp":      p.get("edge_pp",0),
                "model_prob":   p.get("model_prob_pct",0),
                "result":       p.get("result","P"),
                "home_score":   p.get("final_home_score"),
                "away_score":   p.get("final_away_score"),
                "actual_winner":p.get("actual_winner",""),
                "units_profit": p.get("units_profit"),
                "pitcher_matchup": f"{p.get('away_pitcher','')} vs {p.get('home_pitcher','')}",
            }
            for p in yesterday_picks
        ],
        "history": [
            {
                "date":      p.get("date",""),
                "team":      p.get("team",""),
                "odds":      p.get("best_odds",""),
                "edge":      p.get("edge_pp",0),
                "result":    p.get("result",""),
                "profit":    p.get("units_profit"),
            }
            for p in history
        ]
    }

    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results pushed to {RESULTS_FILE}")
    print(f"  Record: {len(wins)}-{len(losses)} | Win Rate: {win_rate*100:.1f}% | ROI: {roi:+.1f}%\n")

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description="The Daily Degenerate — Results Tracker")
    p.add_argument("--log",     action="store_true", help="Log today's picks from latest export")
    p.add_argument("--settle",  action="store_true", help="Check scores and settle pending picks")
    p.add_argument("--summary", action="store_true", help="Print running record and ROI")
    p.add_argument("--push",    action="store_true", help="Update exports/results.json for dashboard")
    p.add_argument("--all",     action="store_true", help="Log + settle + push (run nightly)")
    return p

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.all:
        log_picks()
        settle_picks()
        push_results()
        print_summary()
        return

    if args.log:     log_picks()
    if args.settle:  settle_picks()
    if args.push:    push_results()
    if args.summary: print_summary()

    if not any([args.log, args.settle, args.push, args.summary, args.all]):
        print("\n  Usage:")
        print("    python tracker.py --log        # log today's picks")
        print("    python tracker.py --settle     # check scores, mark W/L")
        print("    python tracker.py --summary    # print record and ROI")
        print("    python tracker.py --push       # update dashboard")
        print("    python tracker.py --all        # do everything (run nightly)\n")

if __name__ == "__main__":
    main()
