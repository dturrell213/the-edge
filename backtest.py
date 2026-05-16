#!/usr/bin/env python3
"""
Best Bets Backtest Engine
Pulls 3 years of MLB historical game data and tests the model against real outcomes.

Steps:
  1. Pull all games from 2023, 2024, 2025 from MLB Stats API
  2. For each game, pull pitcher stats and team records AT THAT DATE
  3. Run the model and predict the winner
  4. Compare prediction to actual outcome
  5. Generate full performance report

Usage:
  python backtest.py --pull          # pull historical data (run once, takes 20-30 min)
  python backtest.py --run           # run backtest on pulled data
  python backtest.py --report        # show performance report
  python backtest.py --pull --run    # do both in one shot
"""

import os, json, time, argparse
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict
import requests

MLB_BASE = "https://statsapi.mlb.com/api/v1"
DATA_DIR = Path("backtest_data")
GAMES_FILE = DATA_DIR / "historical_games.json"
RESULTS_FILE = DATA_DIR / "backtest_results.json"

# League averages by season
SEASON_AVGS = {
    2023: {"era": 4.33, "whip": 1.30, "ops": 0.723, "fip": 4.21},
    2024: {"era": 4.15, "whip": 1.28, "ops": 0.718, "fip": 4.08},
    2025: {"era": 4.20, "whip": 1.30, "ops": 0.720, "fip": 4.20},
}

PARK_FACTORS = {
    "Coors Field": 1.15, "Great American Ball Park": 1.08,
    "Yankee Stadium": 1.07, "Globe Life Field": 1.05,
    "Citizens Bank Park": 1.05, "American Family Field": 1.04,
    "Fenway Park": 1.03, "Wrigley Field": 1.02,
    "Truist Park": 1.01, "Kauffman Stadium": 0.98,
    "Comerica Park": 0.97, "Guaranteed Rate Field": 0.97,
    "Rate Field": 0.97, "Target Field": 0.97,
    "T-Mobile Park": 0.96, "Minute Maid Park": 0.96,
    "Daikin Park": 0.96, "Busch Stadium": 0.96,
    "Petco Park": 0.92, "Oracle Park": 0.94,
    "loanDepot park": 0.95, "PNC Park": 0.96,
    "Progressive Field": 0.98, "Oriole Park at Camden Yards": 0.99,
    "Rogers Centre": 1.03, "Tropicana Field": 0.97,
    "Chase Field": 1.06, "Citi Field": 0.96,
    "Nationals Park": 1.00, "Sacramento": 1.02,
    "Angel Stadium": 0.98, "Dodger Stadium": 0.97,
    "UNIQLO Field at Dodger Stadium": 0.97,
}

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def sf(v, d=0.0):
    try:
        s = str(v).strip()
        if s in ["-.--", "-.-", "---", "", "None", "null"]:
            return float(d)
        return float(s)
    except Exception:
        return float(d)

def si(v, d=0):
    try:
        return int(float(str(v).strip())) if str(v).strip() not in ["","None","null"] else int(d)
    except Exception:
        return int(d)

def setup():
    DATA_DIR.mkdir(exist_ok=True)

def get_with_retry(url, params=None, retries=3, delay=1.0):
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                print(f"  Rate limited, waiting 10s...")
                time.sleep(10)
            else:
                time.sleep(delay)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    return None

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: PULL HISTORICAL GAMES
# ─────────────────────────────────────────────────────────────────────────────

def pull_season(year):
    """Pull all regular season games for a given year."""
    print(f"\n  Pulling {year} season games...")

    data = get_with_retry(f"{MLB_BASE}/schedule", params={
        "sportId": 1,
        "season": str(year),
        "gameType": "R",
        "hydrate": "probablePitcher,linescore,decisions,team",
        "startDate": f"{year}-03-01",
        "endDate": f"{year}-10-31",
    })

    if not data:
        print(f"  Failed to pull {year} schedule")
        return []

    games = []
    for date_entry in data.get("dates", []):
        game_date = date_entry.get("date", "")
        for game in date_entry.get("games", []):
            # Only completed games
            status = game.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue

            game_pk = game.get("gamePk")
            venue = game.get("venue", {}).get("name", "")

            home = game["teams"]["home"]
            away = game["teams"]["away"]

            home_score = home.get("score", 0)
            away_score = away.get("score", 0)

            if home_score is None or away_score is None:
                continue

            home_win = 1 if home_score > away_score else 0

            home_pitcher = home.get("probablePitcher", {})
            away_pitcher = away.get("probablePitcher", {})

            games.append({
                "game_pk": game_pk,
                "date": game_date,
                "year": year,
                "venue": venue,
                "park_factor": PARK_FACTORS.get(venue, 1.0),
                "home_team": home["team"]["name"],
                "away_team": away["team"]["name"],
                "home_team_id": home["team"]["id"],
                "away_team_id": away["team"]["id"],
                "home_score": home_score,
                "away_score": away_score,
                "home_win": home_win,
                "home_pitcher_id": home_pitcher.get("id"),
                "home_pitcher_name": home_pitcher.get("fullName", "TBD"),
                "away_pitcher_id": away_pitcher.get("id"),
                "away_pitcher_name": away_pitcher.get("fullName", "TBD"),
                # Stats to be filled in later
                "home_era": None, "home_whip": None, "home_fip": None,
                "away_era": None, "away_whip": None, "away_fip": None,
                "home_win_pct": None, "away_win_pct": None,
                "home_run_diff": None, "away_run_diff": None,
                "home_ops": None, "away_ops": None,
                "model_home_prob": None,
                "predicted_home_win": None,
                "correct": None,
            })

    print(f"  Found {len(games)} completed games in {year}")
    return games

def pull_pitcher_stats_at_date(pitcher_id, game_date, year):
    """Pull a pitcher's stats up to a specific date."""
    if not pitcher_id:
        return {}
    try:
        data = get_with_retry(f"{MLB_BASE}/people/{pitcher_id}/stats", params={
            "stats": "gameLog",
            "group": "pitching",
            "season": str(year),
            "gameType": "R",
        })
        if not data:
            return {}

        splits = data.get("stats", [{}])[0].get("splits", [])

        # Filter to games before this date
        target = datetime.strptime(game_date, "%Y-%m-%d").date()
        prior_games = [s for s in splits
                      if datetime.strptime(s.get("date","2000-01-01"), "%Y-%m-%d").date() < target]

        if not prior_games:
            return {}

        # Calculate cumulative stats
        total_ip = 0
        total_er = 0
        total_bb = 0
        total_k  = 0
        total_h  = 0
        total_hr = 0
        total_bf = 0

        for g in prior_games:
            stat = g.get("stat", {})
            ip_str = str(stat.get("inningsPitched", "0"))
            try:
                ip_parts = ip_str.split(".")
                ip = int(ip_parts[0]) + (int(ip_parts[1]) / 3 if len(ip_parts) > 1 else 0)
            except Exception:
                ip = 0
            total_ip += ip
            total_er += si(stat.get("earnedRuns", 0))
            total_bb += si(stat.get("baseOnBalls", 0))
            total_k  += si(stat.get("strikeOuts", 0))
            total_h  += si(stat.get("hits", 0))
            total_hr += si(stat.get("homeRuns", 0))
            total_bf += si(stat.get("battersFaced", 0))

        if total_ip < 5:
            return {}

        era  = (total_er * 9) / total_ip if total_ip > 0 else 4.50
        whip = (total_bb + total_h) / total_ip if total_ip > 0 else 1.35
        k9   = (total_k * 9) / total_ip if total_ip > 0 else 8.0
        bb9  = (total_bb * 9) / total_ip if total_ip > 0 else 3.5
        hr9  = (total_hr * 9) / total_ip if total_ip > 0 else 1.2

        # FIP approximation
        fip = ((13*total_hr + 3*total_bb - 2*total_k) / total_ip + 3.10) if total_ip > 0 else 4.50

        return {
            "era": era, "whip": whip, "fip": fip,
            "k_per_9": k9, "bb_per_9": bb9, "hr_per_9": hr9,
            "innings_pitched": total_ip,
            "games_started": len(prior_games),
        }
    except Exception:
        return {}

def pull_team_record_at_date(team_id, game_date, year):
    """Pull team's record and run differential up to a specific date."""
    try:
        data = get_with_retry(f"{MLB_BASE}/teams/{team_id}/stats", params={
            "stats": "gameLog",
            "group": "hitting",
            "season": str(year),
            "gameType": "R",
        })

        # Get schedule to calculate record
        sched = get_with_retry(f"{MLB_BASE}/schedule", params={
            "sportId": 1,
            "teamId": str(team_id),
            "season": str(year),
            "gameType": "R",
            "startDate": f"{year}-03-01",
            "endDate": game_date,
            "hydrate": "linescore,team",
        })

        if not sched:
            return {}

        wins = 0
        losses = 0
        runs_scored = 0
        runs_allowed = 0

        target = datetime.strptime(game_date, "%Y-%m-%d").date()

        for date_entry in sched.get("dates", []):
            gdate = datetime.strptime(date_entry.get("date","2000-01-01"), "%Y-%m-%d").date()
            if gdate >= target:
                continue
            for game in date_entry.get("games", []):
                if game.get("status", {}).get("abstractGameState","") != "Final":
                    continue
                home = game["teams"]["home"]
                away = game["teams"]["away"]
                is_home = home["team"]["id"] == team_id
                team_data = home if is_home else away
                opp_data  = away if is_home else home

                team_score = team_data.get("score", 0) or 0
                opp_score  = opp_data.get("score", 0) or 0

                if team_score > opp_score:
                    wins += 1
                elif opp_score > team_score:
                    losses += 1

                runs_scored  += team_score
                runs_allowed += opp_score

        total = wins + losses
        win_pct = wins / total if total > 0 else 0.5
        run_diff = runs_scored - runs_allowed
        rpg = runs_scored / total if total > 0 else 4.5

        return {
            "wins": wins, "losses": losses, "win_pct": win_pct,
            "run_differential": run_diff, "runs_per_game": rpg,
            "games_played": total,
        }
    except Exception:
        return {}

def pull_all_historical_data():
    """Pull all historical game data for 2023-2025."""
    setup()
    all_games = []

    for year in [2023, 2024, 2025]:
        games = pull_season(year)
        all_games.extend(games)
        time.sleep(1)

    print(f"\n  Total games pulled: {len(all_games)}")
    print(f"  Now enriching with pitcher and team stats...")
    print(f"  This will take 20-30 minutes. Please wait...\n")

    total = len(all_games)
    enriched = 0

    for i, game in enumerate(all_games):
        if i % 100 == 0:
            pct = (i / total) * 100
            print(f"  Progress: {i}/{total} ({pct:.0f}%) — saving checkpoint...")
            with open(GAMES_FILE, "w") as f:
                json.dump(all_games, f)

        year = game["year"]
        avgs = SEASON_AVGS.get(year, SEASON_AVGS[2025])
        gdate = game["date"]

        # Home pitcher stats
        hp_stats = pull_pitcher_stats_at_date(game["home_pitcher_id"], gdate, year)
        game["home_era"]  = hp_stats.get("era", avgs["era"])
        game["home_whip"] = hp_stats.get("whip", avgs["whip"])
        game["home_fip"]  = hp_stats.get("fip", avgs["fip"])
        game["home_k9"]   = hp_stats.get("k_per_9", 8.5)
        game["home_bb9"]  = hp_stats.get("bb_per_9", 3.2)
        game["home_pitcher_ip"] = hp_stats.get("innings_pitched", 0)

        time.sleep(0.2)

        # Away pitcher stats
        ap_stats = pull_pitcher_stats_at_date(game["away_pitcher_id"], gdate, year)
        game["away_era"]  = ap_stats.get("era", avgs["era"])
        game["away_whip"] = ap_stats.get("whip", avgs["whip"])
        game["away_fip"]  = ap_stats.get("fip", avgs["fip"])
        game["away_k9"]   = ap_stats.get("k_per_9", 8.5)
        game["away_bb9"]  = ap_stats.get("bb_per_9", 3.2)
        game["away_pitcher_ip"] = ap_stats.get("innings_pitched", 0)

        time.sleep(0.2)

        # Home team record
        ht_stats = pull_team_record_at_date(game["home_team_id"], gdate, year)
        game["home_win_pct"]  = ht_stats.get("win_pct", 0.5)
        game["home_run_diff"] = ht_stats.get("run_differential", 0)
        game["home_games_played"] = ht_stats.get("games_played", 0)
        game["home_rpg"] = ht_stats.get("runs_per_game", 4.5)

        time.sleep(0.2)

        # Away team record
        at_stats = pull_team_record_at_date(game["away_team_id"], gdate, year)
        game["away_win_pct"]  = at_stats.get("win_pct", 0.5)
        game["away_run_diff"] = at_stats.get("run_differential", 0)
        game["away_games_played"] = at_stats.get("games_played", 0)
        game["away_rpg"] = at_stats.get("runs_per_game", 4.5)

        time.sleep(0.2)
        enriched += 1

    # Final save
    with open(GAMES_FILE, "w") as f:
        json.dump(all_games, f, indent=2)

    print(f"\n  Done! Saved {len(all_games)} games to {GAMES_FILE}")
    return all_games

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: RUN BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def pitcher_score(era, whip, fip, k9, bb9, avg_era, avg_whip, avg_fip):
    """Score pitcher quality vs league average."""
    if era == avg_era and whip == avg_whip:
        return 0.0
    era_s  = (avg_era - era) / avg_era
    whip_s = (avg_whip - whip) / avg_whip
    fip_s  = (avg_fip - fip) / avg_fip
    k_s    = (k9 - 8.5) / 8.5
    bb_s   = (3.2 - bb9) / 3.2
    raw = era_s*0.25 + whip_s*0.20 + fip_s*0.30 + k_s*0.15 + bb_s*0.10
    return max(-1.0, min(1.0, raw))

def streak_adj(streak):
    if streak >= 5: return 0.025
    elif streak >= 3: return 0.012
    elif streak <= -5: return -0.025
    elif streak <= -3: return -0.012
    return 0.0

def rdiff_adj(rdiff, games):
    if not games: return 0.0
    rdpg = rdiff / games
    if rdpg > 1.5: return 0.04
    elif rdpg > 0.75: return 0.02
    elif rdpg > 0: return 0.01
    elif rdpg < -1.5: return -0.04
    elif rdpg < -0.75: return -0.02
    elif rdpg < 0: return -0.01
    return 0.0

def calculate_model_prob(game, weights=None):
    """
    Calculate model win probability for home team.
    Weights can be tuned during ML training.
    """
    year = game.get("year", 2025)
    avgs = SEASON_AVGS.get(year, SEASON_AVGS[2025])

    if weights is None:
        weights = {
            "win_pct": 0.22,
            "pitcher": 0.28,
            "run_diff": 0.15,
            "home_advantage": 0.040,
        }

    home_win_pct = game.get("home_win_pct", 0.5)
    away_win_pct = game.get("away_win_pct", 0.5)

    home_p_score = pitcher_score(
        game.get("home_era", avgs["era"]),
        game.get("home_whip", avgs["whip"]),
        game.get("home_fip", avgs["fip"]),
        game.get("home_k9", 8.5),
        game.get("home_bb9", 3.2),
        avgs["era"], avgs["whip"], avgs["fip"]
    )
    away_p_score = pitcher_score(
        game.get("away_era", avgs["era"]),
        game.get("away_whip", avgs["whip"]),
        game.get("away_fip", avgs["fip"]),
        game.get("away_k9", 8.5),
        game.get("away_bb9", 3.2),
        avgs["era"], avgs["whip"], avgs["fip"]
    )

    park_f = game.get("park_factor", 1.0)
    park_adj = (1.0 - park_f) * 0.02

    home_score = (
        home_win_pct * weights["win_pct"] +
        home_p_score * weights["pitcher"] +
        rdiff_adj(game.get("home_run_diff",0), game.get("home_games_played",1)) +
        park_adj +
        weights["home_advantage"]
    )
    away_score = (
        away_win_pct * weights["win_pct"] +
        away_p_score * weights["pitcher"] +
        rdiff_adj(game.get("away_run_diff",0), game.get("away_games_played",1))
    )

    total = home_score + away_score
    if total <= 0: return 0.5
    return home_score / total

def run_backtest(threshold=3.0, min_odds=-200, max_odds=350):
    """Run the model on all historical games and calculate performance."""
    if not GAMES_FILE.exists():
        print("  No historical data found. Run with --pull first.")
        return

    print(f"\n  Loading historical games...")
    with open(GAMES_FILE) as f:
        games = json.load(f)

    print(f"  Loaded {len(games)} games")
    print(f"  Running model simulation...\n")

    results = []
    skipped = 0

    for game in games:
        # Skip games without enough data
        if game.get("home_games_played", 0) < 10:
            skipped += 1
            continue
        if game.get("home_pitcher_ip", 0) < 5 and game.get("away_pitcher_ip", 0) < 5:
            skipped += 1
            continue

        home_prob = calculate_model_prob(game)
        away_prob = 1 - home_prob

        home_win = game.get("home_win", 0)

        # Simulate: would we have bet this game?
        # We bet when model probability is significantly higher than 50%
        edge_threshold = threshold / 100

        if home_prob - 0.5 >= edge_threshold:
            # Model likes home team
            results.append({
                "date": game["date"],
                "year": game["year"],
                "game": f"{game['away_team']} vs {game['home_team']}",
                "bet_team": game["home_team"],
                "bet_side": "home",
                "model_prob": round(home_prob * 100, 2),
                "edge": round((home_prob - 0.5) * 100, 2),
                "actual_winner": game["home_team"] if home_win else game["away_team"],
                "correct": 1 if home_win else 0,
                "venue": game.get("venue", ""),
                "park_factor": game.get("park_factor", 1.0),
                "home_era": game.get("home_era"),
                "away_era": game.get("away_era"),
                "home_fip": game.get("home_fip"),
                "away_fip": game.get("away_fip"),
            })
        elif away_prob - 0.5 >= edge_threshold:
            # Model likes away team
            results.append({
                "date": game["date"],
                "year": game["year"],
                "game": f"{game['away_team']} vs {game['home_team']}",
                "bet_team": game["away_team"],
                "bet_side": "away",
                "model_prob": round(away_prob * 100, 2),
                "edge": round((away_prob - 0.5) * 100, 2),
                "actual_winner": game["home_team"] if home_win else game["away_team"],
                "correct": 1 if not home_win else 0,
                "venue": game.get("venue", ""),
                "park_factor": game.get("park_factor", 1.0),
                "home_era": game.get("home_era"),
                "away_era": game.get("away_era"),
                "home_fip": game.get("home_fip"),
                "away_fip": game.get("away_fip"),
            })

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"  Skipped {skipped} games (insufficient data)")
    print(f"  Simulated {len(results)} bets\n")
    print_report(results)
    return results

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: PERFORMANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def print_report(results=None):
    """Print full performance report."""
    if results is None:
        if not RESULTS_FILE.exists():
            print("  No backtest results found. Run with --run first.")
            return
        with open(RESULTS_FILE) as f:
            results = json.load(f)

    if not results:
        print("  No results to report.")
        return

    total    = len(results)
    wins     = sum(r["correct"] for r in results)
    losses   = total - wins
    win_rate = wins / total if total > 0 else 0

    # ROI calculation (assuming -110 odds average = 1.909 decimal)
    # Win: profit = 100, Loss: cost = 110
    avg_decimal = 1.909
    profit = wins * 100 - losses * 110
    roi = (profit / (total * 110)) * 100

    # Break down by year
    by_year = defaultdict(lambda: {"wins":0,"total":0})
    for r in results:
        y = r.get("year", 2025)
        by_year[y]["total"] += 1
        by_year[y]["wins"] += r["correct"]

    # Break down by edge bucket
    buckets = defaultdict(lambda: {"wins":0,"total":0})
    for r in results:
        edge = r.get("edge", 0)
        if edge < 5: bucket = "3-5% edge"
        elif edge < 10: bucket = "5-10% edge"
        elif edge < 15: bucket = "10-15% edge"
        else: bucket = "15%+ edge"
        buckets[bucket]["total"] += 1
        buckets[bucket]["wins"] += r["correct"]

    # Home vs away performance
    home_bets = [r for r in results if r["bet_side"] == "home"]
    away_bets = [r for r in results if r["bet_side"] == "away"]
    home_wins = sum(r["correct"] for r in home_bets)
    away_wins = sum(r["correct"] for r in away_bets)

    # Park factor performance
    coors = [r for r in results if "Coors" in r.get("venue","")]
    non_coors = [r for r in results if "Coors" not in r.get("venue","")]

    print(f"\n{'='*65}")
    print(f"  BACKTEST PERFORMANCE REPORT — 2023-2025 MLB")
    print(f"{'='*65}\n")

    print(f"  OVERALL RECORD")
    print(f"  {'─'*40}")
    print(f"  Total bets:     {total}")
    print(f"  Wins:           {wins}")
    print(f"  Losses:         {losses}")
    print(f"  Win rate:       {win_rate*100:.1f}%")
    print(f"  Est. ROI:       {roi:+.1f}%  (at avg -110 odds)")
    print(f"  Est. profit:    ${profit:+,.0f}  (per $110 bet)\n")

    print(f"  BY YEAR")
    print(f"  {'─'*40}")
    for year in sorted(by_year.keys()):
        y = by_year[year]
        wr = y["wins"]/y["total"]*100 if y["total"] > 0 else 0
        print(f"  {year}: {y['wins']}-{y['total']-y['wins']} ({wr:.1f}% win rate)")

    print(f"\n  BY EDGE SIZE")
    print(f"  {'─'*40}")
    for bucket in ["3-5% edge","5-10% edge","10-15% edge","15%+ edge"]:
        b = buckets.get(bucket, {"wins":0,"total":0})
        if b["total"] > 0:
            wr = b["wins"]/b["total"]*100
            print(f"  {bucket}: {b['wins']}-{b['total']-b['wins']} ({wr:.1f}%)")

    print(f"\n  HOME vs AWAY BETS")
    print(f"  {'─'*40}")
    if home_bets:
        print(f"  Home bets: {home_wins}-{len(home_bets)-home_wins} ({home_wins/len(home_bets)*100:.1f}%)")
    if away_bets:
        print(f"  Away bets: {away_wins}-{len(away_bets)-away_wins} ({away_wins/len(away_bets)*100:.1f}%)")

    if coors:
        coors_wins = sum(r["correct"] for r in coors)
        print(f"\n  COORS FIELD GAMES")
        print(f"  {'─'*40}")
        print(f"  Record: {coors_wins}-{len(coors)-coors_wins} ({coors_wins/len(coors)*100:.1f}%)")

    print(f"\n{'='*65}")
    print(f"  Breakeven win rate at -110 odds: 52.4%")
    if win_rate >= 0.524:
        print(f"  ✓ Model is PROFITABLE at {win_rate*100:.1f}% win rate")
    else:
        print(f"  ✗ Model needs tuning — currently at {win_rate*100:.1f}% win rate")
    print(f"{'='*65}\n")

    # Save CSV summary
    csv_path = DATA_DIR / "backtest_summary.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"  Full results saved to {csv_path}\n")

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

import csv

def build_parser():
    p = argparse.ArgumentParser(description="Best Bets Backtest Engine")
    p.add_argument("--pull", action="store_true", help="Pull historical game data")
    p.add_argument("--run", action="store_true", help="Run backtest on pulled data")
    p.add_argument("--report", action="store_true", help="Show performance report")
    p.add_argument("--threshold", type=float, default=3.0, help="Min edge %% to simulate bet")
    return p

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.pull:
        pull_all_historical_data()

    if args.run:
        run_backtest(threshold=args.threshold)

    if args.report and not args.run:
        print_report()

    if not any([args.pull, args.run, args.report]):
        print("\n  Usage:")
        print("    python backtest.py --pull          # pull 3 years of data (20-30 min)")
        print("    python backtest.py --run           # run backtest simulation")
        print("    python backtest.py --report        # show performance report")
        print("    python backtest.py --pull --run    # do both\n")

if __name__ == "__main__":
    main()