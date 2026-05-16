#!/usr/bin/env python3
"""
Sports Betting Value Engine v6
Full data model: FanGraphs + Statcast + MLB Stats API + Weather

Data layers:
  1. MLB Standings       — home/away pct, day/night, L10, streak, run diff
  2. FanGraphs Batters   — wRC+, ISO, xwOBA, xBA, xSLG, BB%, K%, O-Swing%,
                           Z-Contact%, SwStr%, C+SwStr%, EV, Barrel%, HardHit%,
                           Pull%, FB%, GB%, LD%, Hard%, WAR, plus all standard
                           stats: AVG, OBP, SLG, OPS, HR, RBI, H, 2B, 3B, BB, SO, SB
  3. FanGraphs Pitchers  — FIP, xFIP, SIERA, xERA, ERA-, K%, BB%, K-BB%,
                           HR/FB, SwStr%, C+SwStr%, O-Swing%, Zone%,
                           EV allowed, Barrel% allowed, HardHit% allowed,
                           fastball velocity, pitch arsenal
  4. MLB Roster Splits   — home/away splits, vs LHP/RHP per hitter
  5. Baseball Savant     — pitcher contact quality (EV, HardHit%, Barrel% allowed)
  6. Park Factors        — all 30 MLB stadiums
  7. Weather             — temperature, wind speed, precipitation (Open-Meteo)

Usage:
  python main.py --sport mlb --today
  python main.py --sport mlb --tomorrow --export
  python main.py --today --export
  python main.py --threshold 4
"""

import os, csv, json, argparse, pathlib, io
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
import requests
from dotenv import load_dotenv
from tabulate import tabulate

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ODDS_API_KEY    = os.getenv("ODDS_API_KEY", "")
BALLDONTLIE_KEY = os.getenv("BALLDONTLIE_API_KEY", "")

ODDS_BASE    = "https://api.the-odds-api.com/v4"
MLB_BASE     = "https://statsapi.mlb.com/api/v1"
WEATHER_BASE = "https://api.open-meteo.com/v1/forecast"
FG_BASE      = "https://www.fangraphs.com/api/leaders/major-league/data"
SAVANT_BASE  = "https://baseballsavant.mlb.com/leaderboard/statcast"

LICENSED_US_BOOKS = [
    "draftkings","fanduel","betmgm","caesars","bet365",
    "espnbet","fanatics","hardrockbet","betrivers","thescore","ballybet",
]

SPORTS_MAP = {
    "mlb": "baseball_mlb",
    "nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "nfl": "americanfootball_nfl",
    "mls": "soccer_usa_mls",
}

# League averages (2025/2026 MLB)
MLB_AVG_ERA    = 4.20
MLB_AVG_WHIP   = 1.30
MLB_AVG_OPS    = 0.720
MLB_AVG_WRC    = 100.0
MLB_AVG_FIP    = 4.20
MLB_AVG_XFIP   = 4.20
HOME_ADVANTAGE = 0.040

# Park factors — run index (1.0 = neutral)
PARK_FACTORS = {
    "Coors Field": 1.15,
    "Great American Ball Park": 1.08,
    "Yankee Stadium": 1.07,
    "Globe Life Field": 1.05,
    "Citizens Bank Park": 1.05,
    "American Family Field": 1.04,
    "Fenway Park": 1.03,
    "Wrigley Field": 1.02,
    "Truist Park": 1.01,
    "Kauffman Stadium": 0.98,
    "Comerica Park": 0.97,
    "Rate Field": 0.97,
    "Target Field": 0.97,
    "T-Mobile Park": 0.96,
    "Daikin Park": 0.96,
    "Busch Stadium": 0.96,
    "Petco Park": 0.92,
    "Oracle Park": 0.94,
    "loanDepot park": 0.95,
    "PNC Park": 0.96,
    "Progressive Field": 0.98,
    "Oriole Park at Camden Yards": 0.99,
    "Rogers Centre": 1.03,
    "Tropicana Field": 0.97,
    "Chase Field": 1.06,
    "Citi Field": 0.96,
    "Nationals Park": 1.00,
    "Sutter Health Park": 1.02,
    "Angel Stadium": 0.98,
    "UNIQLO Field at Dodger Stadium": 0.97,
}

VENUE_COORDS = {
    "Coors Field": (39.7559,-104.9942),
    "Great American Ball Park": (39.0979,-84.5082),
    "Yankee Stadium": (40.8296,-73.9262),
    "Globe Life Field": (32.7473,-97.0832),
    "Citizens Bank Park": (39.9061,-75.1665),
    "American Family Field": (43.0280,-87.9712),
    "Fenway Park": (42.3467,-71.0972),
    "Wrigley Field": (41.9484,-87.6553),
    "Truist Park": (33.8908,-84.4678),
    "Kauffman Stadium": (39.0517,-94.4803),
    "Comerica Park": (42.3390,-83.0485),
    "Rate Field": (41.8319,-87.6341),
    "Target Field": (44.9817,-93.2787),
    "T-Mobile Park": (47.5914,-122.3325),
    "Daikin Park": (29.7573,-95.3555),
    "Busch Stadium": (38.6226,-90.1928),
    "Petco Park": (32.7076,-117.1570),
    "Oracle Park": (37.7786,-122.3893),
    "loanDepot park": (25.7781,-80.2197),
    "PNC Park": (40.4469,-80.0057),
    "Progressive Field": (41.4962,-81.6852),
    "Oriole Park at Camden Yards": (39.2838,-76.6218),
    "Rogers Centre": (43.6414,-79.3894),
    "Tropicana Field": (27.7682,-82.6534),
    "Chase Field": (33.4453,-112.0667),
    "Citi Field": (40.7571,-73.8458),
    "Nationals Park": (38.8730,-77.0074),
    "Sutter Health Park": (38.5802,-121.5001),
    "Angel Stadium": (33.8003,-117.8827),
    "UNIQLO Field at Dodger Stadium": (34.0739,-118.2400),
}

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def sf(v, d=0.0):
    """Safe float — handles '-.--', None, empty strings."""
    try:
        s = str(v).strip()
        if s in ["-.--", "-.-", "---", "", "None", "null", "-.---", "N/A"]:
            return float(d)
        return float(s)
    except Exception:
        return float(d)

def si(v, d=0):
    """Safe int."""
    try:
        return int(float(str(v).strip())) if str(v).strip() not in ["", "None", "null"] else int(d)
    except Exception:
        return int(d)

def american_to_prob(odds):
    odds = float(odds)
    if odds < 0: return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)

def fmt_american(price):
    price = int(price)
    return f"+{price}" if price > 0 else str(price)

def fmt_time(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%a %b %d  %I:%M %p %Z")
    except Exception:
        return iso

def filter_by_day(events, day_filter):
    if not day_filter: return events
    today = date.today()
    target = today if day_filter == "today" else today + timedelta(days=1)
    return [e for e in events if _event_date(e) == target]

def _event_date(event):
    try:
        iso = event.get("commence_time", "")
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().date()
    except Exception:
        return date.today()

def show_quota(resp):
    remaining = resp.headers.get("x-requests-remaining", "?")
    used = resp.headers.get("x-requests-used", "?")
    print(f"\n  Odds API quota: used {used} | remaining {remaining}\n")
    try:
        rem = int(remaining)
        if rem <= 10: print(f"  ⚠ CRITICAL: Only {rem} requests left!")
        elif rem <= 25: print(f"  ⚠ WARNING: Only {rem} requests remaining.")
    except Exception:
        pass

def check_keys():
    missing = []
    if not ODDS_API_KEY: missing.append("ODDS_API_KEY")
    if not BALLDONTLIE_KEY: missing.append("BALLDONTLIE_API_KEY")
    if missing:
        print(f"\n  ERROR: Missing API keys: {', '.join(missing)}")
        return False
    return True

def export_results(records, metadata):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sport_slug = str(metadata.get("sport","all")).replace(" ","_")
    out_dir = pathlib.Path("exports")
    out_dir.mkdir(exist_ok=True)

    json_path = out_dir / f"ev_{sport_slug}_{ts}.json"
    with open(json_path, "w") as f:
        json.dump({"metadata": metadata, "value_bets": records}, f, indent=2)

    csv_path = out_dir / f"ev_{sport_slug}_{ts}.csv"
    if records:
        flat = []
        for r in records:
            row = dict(r)
            for k in ["home_hitters","away_hitters"]:
                if isinstance(row.get(k), list):
                    row[k] = " | ".join([
                        f"{h.get('name','')} {h.get('avg',0):.3f}/{h.get('ops',0):.3f} OPS {h.get('home_runs',0)}HR {h.get('wrc_plus',0):.0f}wRC+"
                        for h in row[k]
                    ])
            flat.append(row)
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(flat[0].keys()))
            writer.writeheader()
            writer.writerows(flat)

    print(f"\n  Exported {len(records)} record(s):")
    print(f"    JSON → {json_path}")
    print(f"    CSV  → {csv_path}\n")

# ─────────────────────────────────────────────────────────────────────────────
# WEATHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_weather(venue, game_hour_utc):
    coords = VENUE_COORDS.get(venue)
    if not coords: return {}
    try:
        lat, lon = coords
        resp = requests.get(WEATHER_BASE, params={
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m,windspeed_10m,precipitation_probability",
            "timezone": "UTC", "forecast_days": 2,
        }, timeout=10)
        if resp.status_code != 200: return {}
        data = resp.json()
        times  = data["hourly"]["time"]
        temps  = data["hourly"]["temperature_2m"]
        winds  = data["hourly"]["windspeed_10m"]
        precip = data["hourly"]["precipitation_probability"]
        today  = date.today().isoformat()
        for i, t in enumerate(times):
            if today in t and f"T{str(game_hour_utc).zfill(2)}:" in t:
                return {
                    "temp_f": round(temps[i]*9/5+32, 1),
                    "wind_mph": round(winds[i]*0.621371, 1),
                    "precip_pct": precip[i],
                }
        return {
            "temp_f": round(temps[0]*9/5+32, 1),
            "wind_mph": round(winds[0]*0.621371, 1),
            "precip_pct": precip[0],
        }
    except Exception:
        return {}

def weather_factor(w):
    if not w: return 1.0
    f = 1.0
    t = w.get("temp_f", 72)
    wind = w.get("wind_mph", 5)
    rain = w.get("precip_pct", 0)
    if t < 45: f -= 0.04
    elif t < 55: f -= 0.02
    elif t > 85: f += 0.02
    if wind > 20: f += 0.02
    if rain > 50: f -= 0.03
    elif rain > 30: f -= 0.01
    return max(0.90, min(1.10, f))

# ─────────────────────────────────────────────────────────────────────────────
# FANGRAPHS — BATTER DATA
# ─────────────────────────────────────────────────────────────────────────────

def fetch_fg_batters():
    """
    Fetch all qualified batters from FanGraphs.
    Returns dict keyed by MLBAM ID (xMLBAMID).
    Includes all standard stats + advanced metrics.
    """
    print("  Fetching FanGraphs batter data...")
    try:
        resp = requests.get(FG_BASE, params={
            "age": "", "pos": "all", "stats": "bat", "lg": "all",
            "qual": "y", "season": "2026", "season1": "2026",
            "ind": "0", "team": "0", "pageitems": "500",
            "pagenum": "1", "rost": "0", "players": "0",
        }, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://www.fangraphs.com/leaders/major-league"})

        if resp.status_code != 200:
            print(f"  Warning: FanGraphs batters returned {resp.status_code}")
            return {}

        data = resp.json().get("data", [])
        result = {}
        for row in data:
            mlbam_id = si(row.get("xMLBAMID", 0))
            if not mlbam_id:
                continue
            result[mlbam_id] = {
                "name": row.get("PlayerName", ""),
                "team": row.get("TeamNameAbb", ""),
                "position": row.get("position", ""),
                "bats": row.get("Bats", ""),
                # Standard counting stats
                "games": si(row.get("G", 0)),
                "pa": si(row.get("PA", 0)),
                "ab": si(row.get("AB", 0)),
                "hits": si(row.get("H", 0)),
                "doubles": si(row.get("2B", 0)),
                "triples": si(row.get("3B", 0)),
                "home_runs": si(row.get("HR", 0)),
                "runs": si(row.get("R", 0)),
                "rbi": si(row.get("RBI", 0)),
                "walks": si(row.get("BB", 0)),
                "strikeouts": si(row.get("SO", 0)),
                "stolen_bases": si(row.get("SB", 0)),
                # Rate stats
                "avg": sf(row.get("AVG", 0)),
                "obp": sf(row.get("OBP", 0)),
                "slg": sf(row.get("SLG", 0)),
                "ops": sf(row.get("OPS", 0)),
                "iso": sf(row.get("ISO", 0)),
                "babip": sf(row.get("BABIP", 0)),
                "woba": sf(row.get("wOBA", 0)),
                # Advanced/value
                "wrc_plus": sf(row.get("wRC+", 100)),
                "war": sf(row.get("WAR", 0)),
                "wpa": sf(row.get("WPA", 0)),
                "re24": sf(row.get("RE24", 0)),
                # Expected stats
                "x_avg": sf(row.get("xAVG", 0)),
                "x_slg": sf(row.get("xSLG", 0)),
                "x_woba": sf(row.get("xwOBA", 0)),
                # Plate discipline
                "bb_pct": sf(row.get("BB%", 0)),
                "k_pct": sf(row.get("K%", 0)),
                "o_swing_pct": sf(row.get("O-Swing%", 0)),
                "z_contact_pct": sf(row.get("Z-Contact%", 0)),
                "swstr_pct": sf(row.get("SwStr%", 0)),
                "csw_pct": sf(row.get("C+SwStr%", 0)),
                "zone_pct": sf(row.get("Zone%", 0)),
                "contact_pct": sf(row.get("Contact%", 0)),
                # Batted ball
                "fb_pct": sf(row.get("FB%", 0)),
                "gb_pct": sf(row.get("GB%", 0)),
                "ld_pct": sf(row.get("LD%", 0)),
                "hr_fb_pct": sf(row.get("HR/FB", 0)),
                "pull_pct": sf(row.get("Pull%", 0)),
                "cent_pct": sf(row.get("Cent%", 0)),
                "oppo_pct": sf(row.get("Oppo%", 0)),
                "hard_pct": sf(row.get("Hard%", 0)),
                # Statcast
                "exit_velocity": sf(row.get("EV", 0)),
                "launch_angle": sf(row.get("LA", 0)),
                "barrel_pct": sf(row.get("Barrel%", 0)),
                "hard_hit_pct": sf(row.get("HardHit%", 0)),
                "max_ev": sf(row.get("maxEV", 0)),
            }
        print(f"  Loaded {len(result)} FanGraphs batter records")
        return result
    except Exception as e:
        print(f"  Warning: FanGraphs batter error: {e}")
        return {}

# ─────────────────────────────────────────────────────────────────────────────
# FANGRAPHS — PITCHER DATA
# ─────────────────────────────────────────────────────────────────────────────

def fetch_fg_pitchers():
    """
    Fetch all qualified pitchers from FanGraphs.
    Returns dict keyed by MLBAM ID (xMLBAMID).
    """
    print("  Fetching FanGraphs pitcher data...")
    try:
        resp = requests.get(FG_BASE, params={
            "age": "", "pos": "all", "stats": "pit", "lg": "all",
            "qual": "y", "season": "2026", "season1": "2026",
            "ind": "0", "team": "0", "pageitems": "500",
            "pagenum": "1", "rost": "0", "players": "0",
        }, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://www.fangraphs.com/leaders/major-league"})

        if resp.status_code != 200:
            print(f"  Warning: FanGraphs pitchers returned {resp.status_code}")
            return {}

        data = resp.json().get("data", [])
        result = {}
        for row in data:
            mlbam_id = si(row.get("xMLBAMID", 0))
            if not mlbam_id:
                continue
            result[mlbam_id] = {
                "name": row.get("PlayerName", ""),
                "team": row.get("TeamNameAbb", ""),
                "throws": row.get("Throws", "R"),
                # Standard stats
                "wins": si(row.get("W", 0)),
                "losses": si(row.get("L", 0)),
                "era": sf(row.get("ERA", MLB_AVG_ERA), MLB_AVG_ERA),
                "games": si(row.get("G", 0)),
                "games_started": si(row.get("GS", 0)),
                "innings_pitched": sf(row.get("IP", 0)),
                "strikeouts": si(row.get("SO", 0)),
                "walks": si(row.get("BB", 0)),
                "home_runs": si(row.get("HR", 0)),
                "whip": sf(row.get("WHIP", MLB_AVG_WHIP), MLB_AVG_WHIP),
                "babip": sf(row.get("BABIP", 0.300)),
                "lob_pct": sf(row.get("LOB%", 0.720)),
                # Rate stats
                "k_per_9": sf(row.get("K/9", 8.5)),
                "bb_per_9": sf(row.get("BB/9", 3.2)),
                "hr_per_9": sf(row.get("HR/9", 1.2)),
                "h_per_9": sf(row.get("H/9", 9.0)),
                # Advanced ERA estimators
                "fip": sf(row.get("FIP", MLB_AVG_FIP), MLB_AVG_FIP),
                "xfip": sf(row.get("xFIP", MLB_AVG_XFIP), MLB_AVG_XFIP),
                "siera": sf(row.get("SIERA", MLB_AVG_ERA), MLB_AVG_ERA),
                "xera": sf(row.get("xERA", MLB_AVG_ERA), MLB_AVG_ERA),
                "era_minus": sf(row.get("ERA-", 100)),
                "fip_minus": sf(row.get("FIP-", 100)),
                "xfip_minus": sf(row.get("xFIP-", 100)),
                "war": sf(row.get("WAR", 0)),
                # Command metrics
                "k_pct": sf(row.get("K%", 0.22)),
                "bb_pct": sf(row.get("BB%", 0.08)),
                "k_minus_bb_pct": sf(row.get("K-BB%", 0.14)),
                "hr_fb_pct": sf(row.get("HR/FB", 0.10)),
                # Batted ball
                "gb_pct": sf(row.get("GB%", 0.44)),
                "fb_pct": sf(row.get("FB%", 0.35)),
                "ld_pct": sf(row.get("LD%", 0.21)),
                "hard_pct": sf(row.get("Hard%", 0.35)),
                # Pitch discipline
                "o_swing_pct": sf(row.get("O-Swing%", 0.30)),
                "z_contact_pct": sf(row.get("Z-Contact%", 0.85)),
                "swstr_pct": sf(row.get("SwStr%", 0.10)),
                "csw_pct": sf(row.get("C+SwStr%", 0.28)),
                "zone_pct": sf(row.get("Zone%", 0.46)),
                "f_strike_pct": sf(row.get("F-Strike%", 0.60)),
                # Statcast contact allowed
                "exit_velocity_allowed": sf(row.get("EV", 0)),
                "barrel_pct_allowed": sf(row.get("Barrel%", 0)),
                "hard_hit_pct_allowed": sf(row.get("HardHit%", 0)),
                "launch_angle_allowed": sf(row.get("LA", 0)),
                # Pitch arsenal
                "fb_velocity": sf(row.get("FBv", 0)),
                "fb_pct_thrown": sf(row.get("FB%1", 0)),
                "sl_pct": sf(row.get("SL%", 0)),
                "sl_velocity": sf(row.get("SLv", 0)),
                "cb_pct": sf(row.get("CB%", 0)),
                "cb_velocity": sf(row.get("CBv", 0)),
                "ch_pct": sf(row.get("CH%", 0)),
                "ch_velocity": sf(row.get("CHv", 0)),
                # Stuff/command scores (Pitching Bot)
                "pb_stuff": sf(row.get("pb_stuff", 0)),
                "pb_command": sf(row.get("pb_command", 0)),
                "pb_overall": sf(row.get("pb_overall", 0)),
            }
        print(f"  Loaded {len(result)} FanGraphs pitcher records")
        return result
    except Exception as e:
        print(f"  Warning: FanGraphs pitcher error: {e}")
        return {}

# ─────────────────────────────────────────────────────────────────────────────
# MLB STANDINGS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_mlb_standings():
    print("  Fetching MLB standings...")
    try:
        resp = requests.get(f"{MLB_BASE}/standings", params={
            "leagueId": "103,104", "season": "2026",
            "hydrate": "team,record,streak",
        }, timeout=15)
        if resp.status_code != 200: return {}

        standings = {}
        for record in resp.json().get("records", []):
            for tr in record.get("teamRecords", []):
                name    = tr.get("team", {}).get("name", "")
                team_id = tr.get("team", {}).get("id")
                wins    = tr.get("wins", 0)
                losses  = tr.get("losses", 0)
                total   = wins + losses
                pct     = sf(tr.get("winningPercentage","0"), wins/total if total else 0.5)
                rs      = tr.get("runsScored", 0)
                ra      = tr.get("runsAllowed", 0)
                rdiff   = tr.get("runDifferential", 0)
                gp      = tr.get("gamesPlayed", total)

                splits    = tr.get("records", {}).get("splitRecords", [])
                split_map = {s["type"]: s for s in splits}

                def gpct(t):
                    raw = split_map.get(t, {}).get("pct", "0.500")
                    return sf(str(raw).replace(".---","0.500"), 0.500)

                l10    = split_map.get("lastTen", {})
                l10_w  = l10.get("wins", 5)
                l10_l  = l10.get("losses", 5)
                l10_pct = l10_w / (l10_w + l10_l) if (l10_w + l10_l) > 0 else 0.5

                streak     = tr.get("streak", {})
                streak_type= streak.get("streakType", "")
                streak_num = streak.get("streakNumber", 0)
                streak_val = streak_num if streak_type == "wins" else -streak_num

                standings[name] = {
                    "team_id": team_id,
                    "wins": wins, "losses": losses,
                    "overall_pct": pct,
                    "home_pct": gpct("home"),
                    "away_pct": gpct("away"),
                    "day_pct":  gpct("day"),
                    "night_pct":gpct("night"),
                    "last10_pct": l10_pct,
                    "last10_w": l10_w, "last10_l": l10_l,
                    "run_differential": rdiff,
                    "runs_scored": rs, "runs_allowed": ra,
                    "runs_per_game": round(rs/gp,2) if gp else 0,
                    "runs_allowed_per_game": round(ra/gp,2) if gp else 0,
                    "streak": streak_val,
                    "streak_code": streak.get("streakCode",""),
                    "games_played": gp,
                }
        print(f"  Found {len(standings)} teams")
        return standings
    except Exception as e:
        print(f"  Warning: standings error: {e}")
        return {}

# ─────────────────────────────────────────────────────────────────────────────
# PROBABLE PITCHERS + VENUE INFO
# ─────────────────────────────────────────────────────────────────────────────

def fetch_probable_pitchers(game_date, fg_pitchers):
    """
    Fetch today's probable pitchers from MLB schedule.
    Merges with FanGraphs pitcher data using MLBAM ID.
    Returns {team_name: pitcher_dict} and {team_name: venue_dict}
    """
    print("  Fetching probable pitchers...")
    try:
        resp = requests.get(f"{MLB_BASE}/schedule", params={
            "sportId": 1, "date": game_date,
            "hydrate": "probablePitcher(note),venue",
        }, timeout=15)
        if resp.status_code != 200: return {}, {}

        pitchers = {}
        venues   = {}

        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                venue_name   = game.get("venue", {}).get("name", "")
                game_time    = game.get("gameDate", "")

                for side in ["home", "away"]:
                    team_info = game["teams"][side]
                    team_name = team_info["team"]["name"]
                    team_id   = team_info["team"]["id"]
                    pitcher   = team_info.get("probablePitcher", {})

                    venues[team_name] = {
                        "venue": venue_name,
                        "is_home": side == "home",
                        "team_id": team_id,
                        "game_time": game_time,
                    }

                    if not pitcher:
                        pitchers[team_name] = _default_pitcher("TBD")
                        continue

                    mlbam_id    = si(pitcher.get("id", 0))
                    pitcher_name = pitcher.get("fullName", "TBD")

                    # Get FanGraphs data if available
                    fg = fg_pitchers.get(mlbam_id, {})

                    pitchers[team_name] = {
                        "name": pitcher_name,
                        "mlbam_id": mlbam_id,
                        "available": bool(fg),
                        # Standard
                        "era":   fg.get("era", MLB_AVG_ERA),
                        "whip":  fg.get("whip", MLB_AVG_WHIP),
                        "k_per_9": fg.get("k_per_9", 8.5),
                        "bb_per_9": fg.get("bb_per_9", 3.2),
                        "hr_per_9": fg.get("hr_per_9", 1.2),
                        "innings_pitched": fg.get("innings_pitched", 0),
                        "games_started": fg.get("games_started", 0),
                        "wins": fg.get("wins", 0),
                        "losses": fg.get("losses", 0),
                        # Advanced estimators
                        "fip":  fg.get("fip", MLB_AVG_FIP),
                        "xfip": fg.get("xfip", MLB_AVG_XFIP),
                        "siera":fg.get("siera", MLB_AVG_ERA),
                        "xera": fg.get("xera", MLB_AVG_ERA),
                        "era_minus": fg.get("era_minus", 100),
                        "fip_minus": fg.get("fip_minus", 100),
                        # Command
                        "k_pct": fg.get("k_pct", 0.22),
                        "bb_pct": fg.get("bb_pct", 0.08),
                        "k_minus_bb_pct": fg.get("k_minus_bb_pct", 0.14),
                        "hr_fb_pct": fg.get("hr_fb_pct", 0.10),
                        # Stuff
                        "swstr_pct": fg.get("swstr_pct", 0.10),
                        "csw_pct": fg.get("csw_pct", 0.28),
                        "o_swing_pct": fg.get("o_swing_pct", 0.30),
                        "zone_pct": fg.get("zone_pct", 0.46),
                        # Contact quality allowed
                        "ev_allowed": fg.get("exit_velocity_allowed", 87.0),
                        "barrel_pct_allowed": fg.get("barrel_pct_allowed", 6.5),
                        "hard_hit_pct_allowed": fg.get("hard_hit_pct_allowed", 35.0),
                        # Arsenal
                        "fb_velocity": fg.get("fb_velocity", 0),
                        "fb_pct_thrown": fg.get("fb_pct_thrown", 0),
                        "sl_pct": fg.get("sl_pct", 0),
                        "cb_pct": fg.get("cb_pct", 0),
                        "ch_pct": fg.get("ch_pct", 0),
                        "war": fg.get("war", 0),
                        "pb_stuff": fg.get("pb_stuff", 0),
                        "pb_command": fg.get("pb_command", 0),
                        # Summary string
                        "summary": f"{fg.get('wins',0)}-{fg.get('losses',0)} | {fg.get('era', MLB_AVG_ERA):.2f} ERA | {fg.get('fip', MLB_AVG_FIP):.2f} FIP" if fg else "TBD",
                    }

        print(f"  Found pitchers for {len(pitchers)} teams")
        return pitchers, venues
    except Exception as e:
        print(f"  Warning: pitcher fetch error: {e}")
        return {}, {}

def _default_pitcher(name="TBD"):
    return {
        "name": name, "mlbam_id": 0, "available": False,
        "era": MLB_AVG_ERA, "whip": MLB_AVG_WHIP,
        "k_per_9": 8.5, "bb_per_9": 3.2, "hr_per_9": 1.2,
        "innings_pitched": 0, "games_started": 0, "wins": 0, "losses": 0,
        "fip": MLB_AVG_FIP, "xfip": MLB_AVG_XFIP, "siera": MLB_AVG_ERA,
        "xera": MLB_AVG_ERA, "era_minus": 100, "fip_minus": 100,
        "k_pct": 0.22, "bb_pct": 0.08, "k_minus_bb_pct": 0.14, "hr_fb_pct": 0.10,
        "swstr_pct": 0.10, "csw_pct": 0.28, "o_swing_pct": 0.30, "zone_pct": 0.46,
        "ev_allowed": 87.0, "barrel_pct_allowed": 6.5, "hard_hit_pct_allowed": 35.0,
        "fb_velocity": 0, "fb_pct_thrown": 0, "sl_pct": 0, "cb_pct": 0, "ch_pct": 0,
        "war": 0, "pb_stuff": 0, "pb_command": 0, "summary": "TBD",
    }

# ─────────────────────────────────────────────────────────────────────────────
# ROSTER STATS WITH SPLITS (MLB Stats API)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_roster_with_splits(team_id, fg_batters):
    """
    Fetch active roster from MLB Stats API with home/away and vs L/R splits.
    Merges FanGraphs advanced stats by MLBAM ID.
    """
    try:
        resp = requests.get(
            f"{MLB_BASE}/teams/{team_id}/roster",
            params={
                "rosterType": "active", "season": "2026",
                "hydrate": "person(stats(type=[season,homeAndAway,vsLeft,vsRight],group=hitting,season=2026))",
            },
            timeout=20
        )
        if resp.status_code != 200:
            return {"weighted_ops": MLB_AVG_OPS, "weighted_wrc": MLB_AVG_WRC, "players": [], "player_count": 0}

        players = []
        total_pa = 0
        ops_sum  = 0
        wrc_sum  = 0

        for player in resp.json().get("roster", []):
            person = player.get("person", {})
            if player.get("position", {}).get("type","") == "Pitcher":
                continue

            mlbam_id = si(person.get("id", 0))
            stats_blocks = person.get("stats", [])
            season_stats = {}
            home_stats   = {}
            away_stats   = {}
            vsl_stats    = {}
            vsr_stats    = {}

            for block in stats_blocks:
                display = block.get("type", {}).get("displayName","")
                group   = block.get("group",{}).get("displayName","")
                if group != "hitting": continue
                splits = block.get("splits",[])
                if not splits: continue
                if display == "season":
                    season_stats = splits[0].get("stat",{})
                elif display == "homeAndAway":
                    for s in splits:
                        code = s.get("split",{}).get("code","")
                        if code == "H": home_stats = s.get("stat",{})
                        elif code == "A": away_stats = s.get("stat",{})
                elif display == "vsLeft":
                    vsl_stats = splits[0].get("stat",{})
                elif display == "vsRight":
                    vsr_stats = splits[0].get("stat",{})

            if not season_stats: continue
            pa = si(season_stats.get("plateAppearances", 0))
            if pa < 25: continue

            # Merge FanGraphs data
            fg = fg_batters.get(mlbam_id, {})

            ops = sf(season_stats.get("ops","0"), 0)
            wrc_plus = sf(fg.get("wrc_plus", 100), 100)

            p = {
                "name": person.get("fullName","Unknown"),
                "mlbam_id": mlbam_id,
                "position": player.get("position",{}).get("abbreviation",""),
                "bat_side": person.get("batSide",{}).get("code","R"),
                # Season standard stats
                "pa": pa,
                "ab": si(season_stats.get("atBats",0)),
                "hits": si(season_stats.get("hits",0)),
                "home_runs": si(season_stats.get("homeRuns",0)),
                "rbi": si(season_stats.get("rbi",0)),
                "runs": si(season_stats.get("runs",0)),
                "doubles": si(season_stats.get("doubles",0)),
                "triples": si(season_stats.get("triples",0)),
                "strikeouts": si(season_stats.get("strikeOuts",0)),
                "walks": si(season_stats.get("baseOnBalls",0)),
                "stolen_bases": si(season_stats.get("stolenBases",0)),
                "avg": sf(season_stats.get("avg","0"), 0),
                "obp": sf(season_stats.get("obp","0"), 0),
                "slg": sf(season_stats.get("slg","0"), 0),
                "ops": ops,
                "babip": sf(season_stats.get("babip","0"), 0),
                # FanGraphs advanced
                "iso": fg.get("iso", 0),
                "woba": fg.get("woba", 0),
                "x_woba": fg.get("x_woba", 0),
                "x_avg": fg.get("x_avg", 0),
                "x_slg": fg.get("x_slg", 0),
                "wrc_plus": wrc_plus,
                "war": fg.get("war", 0),
                "bb_pct": fg.get("bb_pct", 0),
                "k_pct": fg.get("k_pct", 0),
                "o_swing_pct": fg.get("o_swing_pct", 0),
                "z_contact_pct": fg.get("z_contact_pct", 0),
                "swstr_pct": fg.get("swstr_pct", 0),
                "csw_pct": fg.get("csw_pct", 0),
                "exit_velocity": fg.get("exit_velocity", 0),
                "barrel_pct": fg.get("barrel_pct", 0),
                "hard_hit_pct": fg.get("hard_hit_pct", 0),
                "launch_angle": fg.get("launch_angle", 0),
                "fb_pct": fg.get("fb_pct", 0),
                "gb_pct": fg.get("gb_pct", 0),
                "ld_pct": fg.get("ld_pct", 0),
                "pull_pct": fg.get("pull_pct", 0),
                "hard_pct": fg.get("hard_pct", 0),
                # Home/away splits
                "home_avg": sf(home_stats.get("avg","0"), 0),
                "home_ops": sf(home_stats.get("ops","0"), 0),
                "home_hr": si(home_stats.get("homeRuns",0)),
                "home_obp": sf(home_stats.get("obp","0"), 0),
                "home_slg": sf(home_stats.get("slg","0"), 0),
                "away_avg": sf(away_stats.get("avg","0"), 0),
                "away_ops": sf(away_stats.get("ops","0"), 0),
                "away_hr": si(away_stats.get("homeRuns",0)),
                "away_obp": sf(away_stats.get("obp","0"), 0),
                "away_slg": sf(away_stats.get("slg","0"), 0),
                # vs L/R splits
                "vs_l_avg": sf(vsl_stats.get("avg","0"), 0),
                "vs_l_ops": sf(vsl_stats.get("ops","0"), 0),
                "vs_l_pa": si(vsl_stats.get("plateAppearances",0)),
                "vs_r_avg": sf(vsr_stats.get("avg","0"), 0),
                "vs_r_ops": sf(vsr_stats.get("ops","0"), 0),
                "vs_r_pa": si(vsr_stats.get("plateAppearances",0)),
            }
            players.append(p)
            ops_sum += ops * pa
            wrc_sum += wrc_plus * pa
            total_pa += pa

        players.sort(key=lambda x: x["ops"], reverse=True)
        weighted_ops = ops_sum / total_pa if total_pa > 0 else MLB_AVG_OPS
        weighted_wrc = wrc_sum / total_pa if total_pa > 0 else MLB_AVG_WRC

        return {
            "weighted_ops": weighted_ops,
            "weighted_wrc": weighted_wrc,
            "player_count": len(players),
            "players": players,
        }
    except Exception as e:
        print(f"  Warning: roster error for team {team_id}: {e}")
        return {"weighted_ops": MLB_AVG_OPS, "weighted_wrc": MLB_AVG_WRC, "players": [], "player_count": 0}

# ─────────────────────────────────────────────────────────────────────────────
# TEAM STAFF STATS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_team_stats(team_id):
    result = {}
    for group in ["hitting","pitching"]:
        try:
            resp = requests.get(f"{MLB_BASE}/teams/{team_id}/stats",
                params={"stats":"season","group":group,"season":"2026"}, timeout=10)
            if resp.status_code == 200:
                splits = resp.json().get("stats",[{}])[0].get("splits",[])
                if splits:
                    result[group] = splits[0].get("stat",{})
        except Exception:
            pass
    return result

# ─────────────────────────────────────────────────────────────────────────────
# PROBABILITY MODEL
# ─────────────────────────────────────────────────────────────────────────────

def score_pitcher(p):
    """Score pitcher from -1.0 (terrible) to +1.0 (elite) vs league avg.
    Uses FIP, xFIP, SIERA, K-BB%, SwStr%, and contact quality."""
    if not p.get("available") or p.get("innings_pitched",0) < 5:
        return 0.0

    # ERA estimators (FIP is most predictive)
    fip_s   = (MLB_AVG_FIP - p["fip"])  / MLB_AVG_FIP
    xfip_s  = (MLB_AVG_XFIP - p["xfip"]) / MLB_AVG_XFIP
    siera_s = (MLB_AVG_ERA - p["siera"]) / MLB_AVG_ERA

    # Command
    k_bb_s  = (p["k_minus_bb_pct"] - 0.14) / 0.14
    swstr_s = (p["swstr_pct"] - 0.10) / 0.10

    # Contact quality allowed (lower EV and barrel% = better)
    ev_s      = (87.0 - p["ev_allowed"]) / 87.0 if p["ev_allowed"] > 0 else 0
    barrel_s  = (6.5 - p["barrel_pct_allowed"]) / 6.5 if p["barrel_pct_allowed"] > 0 else 0

    raw = (
        fip_s   * 0.25 +
        xfip_s  * 0.20 +
        siera_s * 0.15 +
        k_bb_s  * 0.15 +
        swstr_s * 0.10 +
        ev_s    * 0.10 +
        barrel_s* 0.05
    )
    return max(-1.0, min(1.0, raw))

def score_lineup(roster):
    """Score lineup using weighted wRC+ and xwOBA."""
    wrc = roster.get("weighted_wrc", MLB_AVG_WRC)
    ops = roster.get("weighted_ops", MLB_AVG_OPS)
    wrc_s = (wrc - MLB_AVG_WRC) / MLB_AVG_WRC
    ops_s = (ops - MLB_AVG_OPS) / MLB_AVG_OPS
    raw = wrc_s * 0.60 + ops_s * 0.40
    return max(-1.0, min(1.0, raw))

def score_staff(team_stats):
    """Score team pitching staff using ERA and WHIP."""
    p = team_stats.get("pitching",{})
    if not p: return 0.0
    era  = sf(p.get("era",  MLB_AVG_ERA), MLB_AVG_ERA)
    whip = sf(p.get("whip", MLB_AVG_WHIP), MLB_AVG_WHIP)
    raw = ((MLB_AVG_ERA - era)/MLB_AVG_ERA)*0.55 + ((MLB_AVG_WHIP - whip)/MLB_AVG_WHIP)*0.45
    return max(-1.0, min(1.0, raw))

def score_offense(team_stats):
    p = team_stats.get("hitting",{})
    if not p: return 0.0
    ops = sf(p.get("ops", MLB_AVG_OPS), MLB_AVG_OPS)
    return max(-1.0, min(1.0, (ops - MLB_AVG_OPS) / MLB_AVG_OPS))

def streak_adj(val):
    if val >= 5: return 0.025
    elif val >= 3: return 0.012
    elif val <= -5: return -0.025
    elif val <= -3: return -0.012
    return 0.0

def rdiff_adj(rdiff, games):
    if not games: return 0.0
    rdpg = rdiff / games
    if rdpg > 1.5: return 0.040
    elif rdpg > 0.75: return 0.020
    elif rdpg > 0: return 0.010
    elif rdpg < -1.5: return -0.040
    elif rdpg < -0.75: return -0.020
    elif rdpg < 0: return -0.010
    return 0.0

def calculate_true_probability(
    home, away, is_day,
    standings, pitchers, venues,
    team_stats_cache, roster_cache, weather_cache
):
    def find(name):
        if name in standings: return standings[name]
        for k, v in standings.items():
            if any(w in k for w in name.split() if len(w) > 3):
                return v
        return {}

    hd = find(home)
    ad = find(away)

    home_base = hd.get("home_pct", 0.500)
    away_base = ad.get("away_pct", 0.500)
    home_form = hd.get("last10_pct", 0.500)
    away_form = ad.get("last10_pct", 0.500)
    home_dn   = hd.get("day_pct" if is_day else "night_pct", home_base)
    away_dn   = ad.get("day_pct" if is_day else "night_pct", away_base)

    home_p = pitchers.get(home, _default_pitcher())
    away_p = pitchers.get(away, _default_pitcher())

    home_tid = hd.get("team_id")
    away_tid = ad.get("team_id")

    home_ts = team_stats_cache.get(home_tid, {})
    away_ts = team_stats_cache.get(away_tid, {})
    home_r  = roster_cache.get(home_tid, {})
    away_r  = roster_cache.get(away_tid, {})

    venue    = venues.get(home, {}).get("venue","")
    park_f   = PARK_FACTORS.get(venue, 1.0)
    park_adj = (1.0 - park_f) * 0.02

    weather  = weather_cache.get(venue, {})
    wx_f     = weather_factor(weather)
    wx_adj   = (1.0 - wx_f) * 0.01

    # MODEL WEIGHTS — tunable
    home_score = (
        home_base * 0.18 +        # home win pct
        home_form * 0.10 +        # last 10 form
        home_dn   * 0.07 +        # day/night split
        score_pitcher(home_p) * 0.22 +   # starting pitcher (FIP/xFIP/SIERA)
        score_staff(home_ts)  * 0.08 +   # bullpen/staff ERA
        score_lineup(home_r)  * 0.14 +   # lineup wRC+ / OPS
        score_offense(home_ts)* 0.07 +   # team offense
        streak_adj(hd.get("streak",0)) +
        rdiff_adj(hd.get("run_differential",0), hd.get("games_played",1)) +
        park_adj + wx_adj + HOME_ADVANTAGE
    )
    away_score = (
        away_base * 0.18 +
        away_form * 0.10 +
        away_dn   * 0.07 +
        score_pitcher(away_p) * 0.22 +
        score_staff(away_ts)  * 0.08 +
        score_lineup(away_r)  * 0.14 +
        score_offense(away_ts)* 0.07 +
        streak_adj(ad.get("streak",0)) +
        rdiff_adj(ad.get("run_differential",0), ad.get("games_played",1))
    )

    total = home_score + away_score
    if total <= 0: return 0.5, 0.5, {}

    home_prob = home_score / total
    away_prob = away_score / total

    expl = {
        # Records
        "home_wins": hd.get("wins",0), "home_losses": hd.get("losses",0),
        "away_wins": ad.get("wins",0), "away_losses": ad.get("losses",0),
        "home_home_pct": round(home_base,3), "away_away_pct": round(away_base,3),
        "home_overall_pct": round(hd.get("overall_pct",0.5),3),
        "away_overall_pct": round(ad.get("overall_pct",0.5),3),
        # Form
        "home_form_l10": f"{hd.get('last10_w',5)}-{hd.get('last10_l',5)}",
        "away_form_l10": f"{ad.get('last10_w',5)}-{ad.get('last10_l',5)}",
        "home_streak": hd.get("streak_code",""),
        "away_streak": ad.get("streak_code",""),
        # Runs
        "home_run_diff": hd.get("run_differential",0),
        "away_run_diff": ad.get("run_differential",0),
        "home_runs_per_game": hd.get("runs_per_game",0),
        "away_runs_per_game": ad.get("runs_per_game",0),
        "home_runs_allowed_per_game": hd.get("runs_allowed_per_game",0),
        "away_runs_allowed_per_game": ad.get("runs_allowed_per_game",0),
        # Starting pitchers — full profile
        "home_pitcher": home_p.get("name","TBD"),
        "away_pitcher": away_p.get("name","TBD"),
        "home_pitcher_summary": home_p.get("summary","N/A"),
        "away_pitcher_summary": away_p.get("summary","N/A"),
        "home_pitcher_era":  home_p.get("era","N/A"),
        "away_pitcher_era":  away_p.get("era","N/A"),
        "home_pitcher_fip":  home_p.get("fip","N/A"),
        "away_pitcher_fip":  away_p.get("fip","N/A"),
        "home_pitcher_xfip": home_p.get("xfip","N/A"),
        "away_pitcher_xfip": away_p.get("xfip","N/A"),
        "home_pitcher_siera":home_p.get("siera","N/A"),
        "away_pitcher_siera":away_p.get("siera","N/A"),
        "home_pitcher_xera": home_p.get("xera","N/A"),
        "away_pitcher_xera": away_p.get("xera","N/A"),
        "home_pitcher_whip": home_p.get("whip","N/A"),
        "away_pitcher_whip": away_p.get("whip","N/A"),
        "home_pitcher_k9":   home_p.get("k_per_9","N/A"),
        "away_pitcher_k9":   away_p.get("k_per_9","N/A"),
        "home_pitcher_bb9":  home_p.get("bb_per_9","N/A"),
        "away_pitcher_bb9":  away_p.get("bb_per_9","N/A"),
        "home_pitcher_k_pct":  home_p.get("k_pct",0),
        "away_pitcher_k_pct":  away_p.get("k_pct",0),
        "home_pitcher_bb_pct": home_p.get("bb_pct",0),
        "away_pitcher_bb_pct": away_p.get("bb_pct",0),
        "home_pitcher_k_bb":   home_p.get("k_minus_bb_pct",0),
        "away_pitcher_k_bb":   away_p.get("k_minus_bb_pct",0),
        "home_pitcher_swstr":  home_p.get("swstr_pct",0),
        "away_pitcher_swstr":  away_p.get("swstr_pct",0),
        "home_pitcher_csw":    home_p.get("csw_pct",0),
        "away_pitcher_csw":    away_p.get("csw_pct",0),
        "home_pitcher_ev_allowed":      home_p.get("ev_allowed",0),
        "away_pitcher_ev_allowed":      away_p.get("ev_allowed",0),
        "home_pitcher_barrel_pct":      home_p.get("barrel_pct_allowed",0),
        "away_pitcher_barrel_pct":      away_p.get("barrel_pct_allowed",0),
        "home_pitcher_hard_hit_pct":    home_p.get("hard_hit_pct_allowed",0),
        "away_pitcher_hard_hit_pct":    away_p.get("hard_hit_pct_allowed",0),
        "home_pitcher_fb_velo": home_p.get("fb_velocity",0),
        "away_pitcher_fb_velo": away_p.get("fb_velocity",0),
        "home_pitcher_ip":  home_p.get("innings_pitched",0),
        "away_pitcher_ip":  away_p.get("innings_pitched",0),
        "home_pitcher_war": home_p.get("war",0),
        "away_pitcher_war": away_p.get("war",0),
        "home_pitcher_pb_stuff":   home_p.get("pb_stuff",0),
        "away_pitcher_pb_stuff":   away_p.get("pb_stuff",0),
        # Staff
        "home_staff_era":  str(home_ts.get("pitching",{}).get("era","N/A")),
        "away_staff_era":  str(away_ts.get("pitching",{}).get("era","N/A")),
        "home_staff_whip": str(home_ts.get("pitching",{}).get("whip","N/A")),
        "away_staff_whip": str(away_ts.get("pitching",{}).get("whip","N/A")),
        "home_staff_k9":   str(home_ts.get("pitching",{}).get("strikeoutsPer9Inn","N/A")),
        "away_staff_k9":   str(away_ts.get("pitching",{}).get("strikeoutsPer9Inn","N/A")),
        # Lineup
        "home_roster_ops": round(home_r.get("weighted_ops",0),3),
        "away_roster_ops": round(away_r.get("weighted_ops",0),3),
        "home_roster_wrc": round(home_r.get("weighted_wrc",0),1),
        "away_roster_wrc": round(away_r.get("weighted_wrc",0),1),
        "home_player_count": home_r.get("player_count",0),
        "away_player_count": away_r.get("player_count",0),
        "home_hitters": home_r.get("players",[]),
        "away_hitters": away_r.get("players",[]),
        # Park/weather
        "venue": venue,
        "park_factor": park_f,
        "weather_temp_f": weather.get("temp_f","N/A"),
        "weather_wind_mph": weather.get("wind_mph","N/A"),
        "weather_precip_pct": weather.get("precip_pct","N/A"),
        "is_day_game": is_day,
    }

    return home_prob, away_prob, expl

# ─────────────────────────────────────────────────────────────────────────────
# ODDS FETCHER
# ─────────────────────────────────────────────────────────────────────────────

def fetch_odds(sport, day_filter):
    params = {
        "apiKey": ODDS_API_KEY, "regions": "us",
        "markets": "h2h", "oddsFormat": "american",
        "dateFormat": "iso", "bookmakers": ",".join(LICENSED_US_BOOKS),
    }
    resp = requests.get(f"{ODDS_BASE}/sports/{sport}/odds", params=params, timeout=20)
    if resp.status_code == 404:
        print(f"  Sport '{sport}' not found or not in season.")
        return []
    if resp.status_code != 200:
        print(f"  ERROR {resp.status_code}")
        return []
    show_quota(resp)
    return filter_by_day(resp.json(), day_filter)

# ─────────────────────────────────────────────────────────────────────────────
# EV CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

def calculate_ev(events, sport, standings, pitchers, venues,
                 team_stats_cache, roster_cache, weather_cache, threshold):
    value_rows    = []
    export_records = []

    for event in events:
        home = event.get("home_team","?")
        away = event.get("away_team","?")
        game_label = f"{away} vs {home}"
        start = fmt_time(event.get("commence_time",""))

        try:
            dt = datetime.fromisoformat(event.get("commence_time","").replace("Z","+00:00")).astimezone()
            is_day = dt.hour < 17
        except Exception:
            is_day = False

        # Collect book odds
        entries = []
        for bk in event.get("bookmakers",[]):
            for mkt in bk.get("markets",[]):
                if mkt["key"] != "h2h": continue
                for outcome in mkt.get("outcomes",[]):
                    price = outcome.get("price")
                    if price is None: continue
                    entries.append((bk["title"], outcome["name"], int(price), american_to_prob(price)))

        if not entries: continue

        # Market average
        outcome_probs = defaultdict(list)
        for _, name, _, prob in entries:
            outcome_probs[name].append(prob)
        market_avg = {name: sum(ps)/len(ps) for name, ps in outcome_probs.items()}

        # Best odds per team
        best_odds_map = {}
        best_book_map = {}
        for bk_title, outcome_name, american_odds, prob in entries:
            if outcome_name not in best_odds_map or american_odds > best_odds_map[outcome_name]:
                best_odds_map[outcome_name] = american_odds
                best_book_map[outcome_name] = bk_title

        # Model probabilities
        model_probs = {}
        expl = {}

        if sport == "baseball_mlb" and standings:
            hp, ap, expl = calculate_true_probability(
                home, away, is_day, standings, pitchers, venues,
                team_stats_cache, roster_cache, weather_cache
            )
            model_probs[home] = hp
            model_probs[away] = ap

        # Find value
        for outcome_name, model_prob in model_probs.items():
            if not model_prob: continue
            best_price = best_odds_map.get(outcome_name)
            best_bk    = best_book_map.get(outcome_name,"")
            if not best_price: continue
            if best_price > 350 or best_price < -350: continue  # filter out fake longshot/futures lines

            best_implied    = american_to_prob(best_price)
            market_avg_prob = market_avg.get(outcome_name, best_implied)
            edge = (model_prob - best_implied) * 100

            if edge >= threshold:
                pitcher_note = f"{expl.get('away_pitcher','TBD')} vs {expl.get('home_pitcher','TBD')}" if expl else ""
                value_rows.append((edge, [
                    game_label, start, outcome_name, best_bk,
                    fmt_american(best_price),
                    f"{best_implied*100:.1f}%",
                    f"{market_avg_prob*100:.1f}%",
                    f"{model_prob*100:.1f}%",
                    f"+{edge:.1f}pp",
                    pitcher_note,
                ]))

                export_records.append({
                    "sport": sport,
                    "game": game_label,
                    "time": start,
                    "team": outcome_name,
                    "best_bookmaker": best_bk,
                    "best_odds": fmt_american(best_price),
                    "best_odds_american": best_price,
                    "best_book_implied_pct": round(best_implied*100,2),
                    "market_avg_prob_pct": round(market_avg_prob*100,2),
                    "model_prob_pct": round(model_prob*100,2),
                    "edge_pp": round(edge,2),
                    # Pitcher matchup
                    "pitcher_matchup": pitcher_note,
                    "home_pitcher": expl.get("home_pitcher",""),
                    "away_pitcher": expl.get("away_pitcher",""),
                    "home_pitcher_summary": expl.get("home_pitcher_summary",""),
                    "away_pitcher_summary": expl.get("away_pitcher_summary",""),
                    "home_pitcher_era":   expl.get("home_pitcher_era",""),
                    "away_pitcher_era":   expl.get("away_pitcher_era",""),
                    "home_pitcher_fip":   expl.get("home_pitcher_fip",""),
                    "away_pitcher_fip":   expl.get("away_pitcher_fip",""),
                    "home_pitcher_xfip":  expl.get("home_pitcher_xfip",""),
                    "away_pitcher_xfip":  expl.get("away_pitcher_xfip",""),
                    "home_pitcher_siera": expl.get("home_pitcher_siera",""),
                    "away_pitcher_siera": expl.get("away_pitcher_siera",""),
                    "home_pitcher_xera":  expl.get("home_pitcher_xera",""),
                    "away_pitcher_xera":  expl.get("away_pitcher_xera",""),
                    "home_pitcher_whip":  expl.get("home_pitcher_whip",""),
                    "away_pitcher_whip":  expl.get("away_pitcher_whip",""),
                    "home_pitcher_k9":    expl.get("home_pitcher_k9",""),
                    "away_pitcher_k9":    expl.get("away_pitcher_k9",""),
                    "home_pitcher_bb9":   expl.get("home_pitcher_bb9",""),
                    "away_pitcher_bb9":   expl.get("away_pitcher_bb9",""),
                    "home_pitcher_k_pct": expl.get("home_pitcher_k_pct",0),
                    "away_pitcher_k_pct": expl.get("away_pitcher_k_pct",0),
                    "home_pitcher_bb_pct":expl.get("home_pitcher_bb_pct",0),
                    "away_pitcher_bb_pct":expl.get("away_pitcher_bb_pct",0),
                    "home_pitcher_k_bb":  expl.get("home_pitcher_k_bb",0),
                    "away_pitcher_k_bb":  expl.get("away_pitcher_k_bb",0),
                    "home_pitcher_swstr": expl.get("home_pitcher_swstr",0),
                    "away_pitcher_swstr": expl.get("away_pitcher_swstr",0),
                    "home_pitcher_csw":   expl.get("home_pitcher_csw",0),
                    "away_pitcher_csw":   expl.get("away_pitcher_csw",0),
                    "home_pitcher_ev_allowed":   expl.get("home_pitcher_ev_allowed",0),
                    "away_pitcher_ev_allowed":   expl.get("away_pitcher_ev_allowed",0),
                    "home_pitcher_barrel_pct":   expl.get("home_pitcher_barrel_pct",0),
                    "away_pitcher_barrel_pct":   expl.get("away_pitcher_barrel_pct",0),
                    "home_pitcher_hard_hit_pct": expl.get("home_pitcher_hard_hit_pct",0),
                    "away_pitcher_hard_hit_pct": expl.get("away_pitcher_hard_hit_pct",0),
                    "home_pitcher_fb_velo": expl.get("home_pitcher_fb_velo",0),
                    "away_pitcher_fb_velo": expl.get("away_pitcher_fb_velo",0),
                    "home_pitcher_ip":  expl.get("home_pitcher_ip",0),
                    "away_pitcher_ip":  expl.get("away_pitcher_ip",0),
                    "home_pitcher_war": expl.get("home_pitcher_war",0),
                    "away_pitcher_war": expl.get("away_pitcher_war",0),
                    # Staff
                    "home_staff_era":  expl.get("home_staff_era",""),
                    "away_staff_era":  expl.get("away_staff_era",""),
                    "home_staff_whip": expl.get("home_staff_whip",""),
                    "away_staff_whip": expl.get("away_staff_whip",""),
                    # Records
                    "home_wins": expl.get("home_wins",0),
                    "home_losses": expl.get("home_losses",0),
                    "away_wins": expl.get("away_wins",0),
                    "away_losses": expl.get("away_losses",0),
                    "home_home_pct": expl.get("home_home_pct",0),
                    "away_away_pct": expl.get("away_away_pct",0),
                    # Form
                    "home_form_l10": expl.get("home_form_l10",""),
                    "away_form_l10": expl.get("away_form_l10",""),
                    "home_streak": expl.get("home_streak",""),
                    "away_streak": expl.get("away_streak",""),
                    # Runs
                    "home_run_diff": expl.get("home_run_diff",0),
                    "away_run_diff": expl.get("away_run_diff",0),
                    "home_runs_per_game": expl.get("home_runs_per_game",0),
                    "away_runs_per_game": expl.get("away_runs_per_game",0),
                    "home_runs_allowed_per_game": expl.get("home_runs_allowed_per_game",0),
                    "away_runs_allowed_per_game": expl.get("away_runs_allowed_per_game",0),
                    # Lineup
                    "home_roster_ops": expl.get("home_roster_ops",0),
                    "away_roster_ops": expl.get("away_roster_ops",0),
                    "home_roster_wrc": expl.get("home_roster_wrc",0),
                    "away_roster_wrc": expl.get("away_roster_wrc",0),
                    "home_player_count": expl.get("home_player_count",0),
                    "away_player_count": expl.get("away_player_count",0),
                    "home_hitters": expl.get("home_hitters",[]),
                    "away_hitters": expl.get("away_hitters",[]),
                    # Park/weather
                    "venue": expl.get("venue",""),
                    "park_factor": expl.get("park_factor",1.0),
                    "weather_temp_f": expl.get("weather_temp_f",""),
                    "weather_wind_mph": expl.get("weather_wind_mph",""),
                    "weather_precip_pct": expl.get("weather_precip_pct",""),
                    "is_day_game": expl.get("is_day_game",False),
                })

    return value_rows, export_records

# ─────────────────────────────────────────────────────────────────────────────
# SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def run_scanner(sport_key, day_filter, threshold, do_export):
    sport_api  = SPORTS_MAP.get(sport_key, sport_key)
    today_str  = date.today().strftime("%Y-%m-%d")
    target_str = (date.today()+timedelta(days=1)).strftime("%Y-%m-%d") if day_filter=="tomorrow" else today_str

    print(f"\n{'='*70}")
    print(f"  SPORTS BETTING VALUE ENGINE v6")
    print(f"  Sport: {sport_api.upper()} | Filter: {day_filter} | Threshold: >{threshold}%")
    print(f"{'='*70}\n")

    standings        = {}
    pitchers         = {}
    venues           = {}
    team_stats_cache = {}
    roster_cache     = {}
    weather_cache    = {}
    fg_batters       = {}
    fg_pitchers_data = {}

    if sport_api == "baseball_mlb":
        standings        = fetch_mlb_standings()
        fg_batters       = fetch_fg_batters()
        fg_pitchers_data = fetch_fg_pitchers()
        pitchers, venues = fetch_probable_pitchers(target_str, fg_pitchers_data)

        print("  Fetching odds...")
        events = fetch_odds(sport_api, day_filter)
        if not events:
            print(f"  No games found for {day_filter}.\n")
            return
        print(f"  Found {len(events)} game(s)\n")

        print("  Fetching team staff stats and full roster with splits...")
        for event in events:
            for team_name in [event.get("home_team",""), event.get("away_team","")]:
                team_id = standings.get(team_name, {}).get("team_id")
                if not team_id:
                    for name, data in standings.items():
                        if any(w in name for w in team_name.split() if len(w) > 3):
                            team_id = data.get("team_id"); break
                if team_id and team_id not in team_stats_cache:
                    team_stats_cache[team_id] = fetch_team_stats(team_id)
                if team_id and team_id not in roster_cache:
                    roster_cache[team_id] = fetch_roster_with_splits(team_id, fg_batters)
                    count = roster_cache[team_id].get("player_count",0)
                    ops   = roster_cache[team_id].get("weighted_ops",0)
                    wrc   = roster_cache[team_id].get("weighted_wrc",0)
                    print(f"    {team_name}: {count} hitters | OPS {ops:.3f} | wRC+ {wrc:.0f}")

        print("\n  Fetching weather...")
        for event in events:
            home  = event.get("home_team","")
            venue = venues.get(home, {}).get("venue","")
            if venue and venue not in weather_cache:
                try:
                    gdt = datetime.fromisoformat(event.get("commence_time","").replace("Z","+00:00"))
                    w = fetch_weather(venue, gdt.hour)
                    if w:
                        weather_cache[venue] = w
                        print(f"    {venue}: {w.get('temp_f')}F, {w.get('wind_mph')} mph, rain {w.get('precip_pct')}%")
                except Exception:
                    pass

    else:
        print("  Fetching odds...")
        events = fetch_odds(sport_api, day_filter)
        if not events:
            print(f"  No games found for {day_filter}.\n")
            return
        print(f"  Found {len(events)} game(s)\n")

    print("\n  Running value model...\n")
    value_rows, export_records = calculate_ev(
        events, sport_api, standings, pitchers, venues,
        team_stats_cache, roster_cache, weather_cache, threshold
    )

    if not value_rows:
        print(f"  No value bets found above {threshold}% threshold.\n")
        return

    value_rows.sort(key=lambda x: x[0], reverse=True)
    headers = ["Game","Time","Team","Best Book","Best Odds","Book Prob","Mkt Avg","Model Prob","Edge","Pitchers"]
    print(f"{'='*100}")
    print(f"  {len(value_rows)} VALUE BET(S) FOUND — ranked by model edge")
    print(f"{'='*100}\n")
    print(tabulate([r for _,r in value_rows], headers=headers, tablefmt="rounded_outline"))
    print(f"""
  Model data layers (v6):
    MLB standings  — home/away pct, last 10, streak, run differential
    FanGraphs      — wRC+, FIP, xFIP, SIERA, xERA, K-BB%, SwStr%, EV, Barrel%
    Roster splits  — home/away and vs L/R per hitter
    Park factors   — all 30 MLB stadiums
    Weather        — temperature, wind, precipitation
""")

    if do_export:
        export_results(export_records, {
            "date": today_str,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sport": sport_api, "day_filter": day_filter,
            "threshold_pct": threshold,
            "total_bets_found": len(export_records),
            "model_version": "v6",
        })

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description="Sports Betting Value Engine v6")
    p.add_argument("--sport", choices=list(SPORTS_MAP.keys()))
    p.add_argument("--threshold", type=float, default=15.0)
    p.add_argument("--export", action="store_true")
    day = p.add_mutually_exclusive_group()
    day.add_argument("--today", action="store_true")
    day.add_argument("--tomorrow", action="store_true")
    return p

def main():
    if not check_keys(): return
    parser = build_parser()
    args   = parser.parse_args()
    day_filter = "today" if args.today else "tomorrow" if args.tomorrow else "today"
    if args.sport:
        run_scanner(args.sport, day_filter, args.threshold, args.export)
    else:
        run_scanner("mlb", day_filter, args.threshold, args.export)

if __name__ == "__main__":
    main()