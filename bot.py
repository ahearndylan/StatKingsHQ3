import tweepy
from nba_api.stats.endpoints import scoreboardv2, playbyplayv2, boxscoretraditionalv2
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import time
import json
import os

# ======================= #
# TWITTER AUTHENTICATION  #
# ======================= #
bearer_token = "AAAAAAAAAAAAAAAAAAAAAPztzwEAAAAAvBGCjApPNyqj9c%2BG7740SkkTShs%3DTCpOQ0DMncSMhaW0OA4UTPZrPRx3BHjIxFPzRyeoyMs2KHk6hM"
api_key = "uKyGoDr5LQbLvu9i7pgFrAnBr"
api_secret = "KGBVtj1BUmAEsyoTmZhz67953ItQ8TIDcChSpodXV8uGMPXsoH"
access_token = "1901441558596988929-WMdEPOtNDj7QTJgLHVylxnylI9ObgD"
access_token_secret = "9sf83R8A0MBdijPdns6nWaG7HF47htcWo6oONPmMS7o98"

client = tweepy.Client(
    bearer_token=bearer_token,
    consumer_key=api_key,
    consumer_secret=api_secret,
    access_token=access_token,
    access_token_secret=access_token_secret
)

# ======================= #
#     TEAM NAME MAPPING   #
# ======================= #
TEAM_NAME_MAP = {
    "ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "CHA": "Hornets",
    "CHI": "Bulls", "CLE": "Cavaliers", "DAL": "Mavericks", "DEN": "Nuggets",
    "DET": "Pistons", "GSW": "Warriors", "HOU": "Rockets", "IND": "Pacers",
    "LAC": "Clippers", "LAL": "Lakers", "MEM": "Grizzlies", "MIA": "Heat",
    "MIL": "Bucks", "MIN": "Timberwolves", "NOP": "Pelicans", "NYK": "Knicks",
    "OKC": "Thunder", "ORL": "Magic", "PHI": "76ers", "PHX": "Suns",
    "POR": "Blazers", "SAC": "Kings", "SAS": "Spurs", "TOR": "Raptors",
    "UTA": "Jazz", "WAS": "Wizards"
}

# ======================= #
#     NBA STATS LOGIC     #
# ======================= #

def get_yesterday_date_str():
    est_now = datetime.now(timezone.utc) - timedelta(hours=4)
    yesterday = est_now - timedelta(days=1)
    return yesterday.strftime("%m/%d/%Y")

def get_game_ids_for_date(date_str, max_retries=3):
    for attempt in range(max_retries):
        try:
            scoreboard = scoreboardv2.ScoreboardV2(game_date=date_str)
            games = scoreboard.get_normalized_dict()["GameHeader"]
            return [game["GAME_ID"] for game in games]
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    raise Exception("Failed to fetch game IDs after multiple attempts.")

def get_player_team_map(game_id):
    boxscore = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
    players = boxscore.get_normalized_dict()["PlayerStats"]
    return {p["PLAYER_ID"]: (p["PLAYER_NAME"], p["TEAM_ABBREVIATION"]) for p in players}

def process_4q_stats(game_id):
    pbp = playbyplayv2.PlayByPlayV2(game_id=game_id)
    events = pbp.get_normalized_dict()["PlayByPlay"]
    player_map = get_player_team_map(game_id)

    stats = defaultdict(lambda: {
        "name": "", "team": "",
        "pts": 0, "fgm": 0, "fga": 0,
        "ast": 0
    })

    for event in events:
        if event["PERIOD"] != 4:
            continue

        action_type = event.get("EVENTMSGTYPE")
        player1_id = event.get("PLAYER1_ID")
        player2_id = event.get("PLAYER2_ID")
        description = event.get("HOMEDESCRIPTION") or event.get("VISITORDESCRIPTION") or ""

        if action_type == 1:
            points = 2
            if "3PT" in description:
                points = 3
            stats[player1_id]["pts"] += points
            stats[player1_id]["fgm"] += 1
            stats[player1_id]["fga"] += 1
        elif action_type == 2:
            if player1_id:
                stats[player1_id]["fga"] += 1
        elif action_type == 3:
            if "MISS" not in description and player1_id:
                stats[player1_id]["pts"] += 1
        elif action_type == 5:
            if player2_id and player2_id in player_map:
                stats[player2_id]["ast"] += 1

        for pid in [player1_id, player2_id]:
            if pid and stats[pid]["name"] == "" and pid in player_map:
                stats[pid]["name"], stats[pid]["team"] = player_map[pid]

    return stats

def get_best_4q_team(date_str):
    scoreboard = scoreboardv2.ScoreboardV2(game_date=date_str)
    linescores = scoreboard.get_normalized_dict()["LineScore"]
    game_diffs = []
    seen_game_ids = set()

    for team in linescores:
        game_id = team["GAME_ID"]
        if game_id in seen_game_ids:
            continue
        seen_game_ids.add(game_id)

        matching = [t for t in linescores if t["GAME_ID"] == game_id]
        if len(matching) == 2:
            t1, t2 = matching
            t1_pts = t1["PTS_QTR4"] or 0
            t2_pts = t2["PTS_QTR4"] or 0
            if t1_pts > t2_pts:
                game_diffs.append((t1["TEAM_ABBREVIATION"], t1_pts - t2_pts))
            elif t2_pts > t1_pts:
                game_diffs.append((t2["TEAM_ABBREVIATION"], t2_pts - t1_pts))

    if game_diffs:
        return max(game_diffs, key=lambda x: x[1])
    return None

def aggregate_leaders(games_stats):
    top_points = {"name": "", "team": "", "stat": 0}
    top_assists = {"name": "", "team": "", "stat": 0}
    top_eff = {"name": "", "team": "", "fg_pct": 0.0, "fga": 0}

    for pid, p in games_stats.items():
        if p["pts"] > top_points["stat"] and p["name"]:
            top_points = {"name": p["name"], "team": p["team"], "stat": p["pts"]}
        if p["ast"] > top_assists["stat"] and p["name"]:
            top_assists = {"name": p["name"], "team": p["team"], "stat": p["ast"]}
        if p["fga"] >= 4:
            fg_pct = p["fgm"] / p["fga"]
            if fg_pct > top_eff["fg_pct"] and p["name"]:
                top_eff = {
                    "name": p["name"],
                    "team": p["team"],
                    "fg_pct": round(fg_pct * 100, 1),
                    "fga": p["fga"]
                }

    return top_points, top_eff, top_assists

def compose_tweet(date_str, points, fg, assists, team_4q_diff):
    tweet = f"""⏱️ Clutch Time Kings – {date_str}

🚀 4Q Scoring Leader
{points['name']} ({points['team']}): {points['stat']} PTS

💎 4Q Efficiency
{fg['name']} ({fg['team']}): {fg['fg_pct']}% FG ({fg['fga']} FGA)

🧠 4Q Assists
{assists['name']} ({assists['team']}): {assists['stat']} AST"""

    if team_4q_diff:
        team_abbr = team_4q_diff[0]
        team_name = TEAM_NAME_MAP.get(team_abbr, team_abbr)
        tweet += f"""\n\n📈 Best 4Q Team
{team_name}: +{team_4q_diff[1]}"""

    tweet += "\n\n#NBA #NBAStats #StatKingsHQ"
    return tweet

# ======================= #
#   WRITE TO clutch.json  #
# ======================= #

def update_clutch_json(date_str, points, fg, assists, team_4q_diff, path="clutch.json"):
    entry = {
        "date": date_str,
        "points": {
            "player": points["name"],
            "team": points["team"],
            "value": points["stat"]
        },
        "fg": {
            "player": fg["name"],
            "team": fg["team"],
            "fg_pct": fg["fg_pct"],
            "fga": fg["fga"]
        },
        "assists": {
            "player": assists["name"],
            "team": assists["team"],
            "value": assists["stat"]
        },
        "team_4q_diff": {
            "abbr": team_4q_diff[0],
            "value": team_4q_diff[1]
        } if team_4q_diff else None
    }

    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
        else:
            data = {"nights": []}

        data["nights"] = [d for d in data["nights"] if d["date"] != date_str]
        data["nights"].insert(0, entry)
        data["nights"] = data["nights"][:30]

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        print(f"✅ Saved to {path}")
    except Exception as e:
        print("❌ Error writing clutch.json:", e)

# ======================= #
#        MAIN BOT         #
# ======================= #

def run_bot():
    date_str = get_yesterday_date_str()
    try:
        game_ids = get_game_ids_for_date(date_str)
        if not game_ids:
            print("No games found for", date_str)
            return

        combined_stats = defaultdict(lambda: {
            "name": "", "team": "",
            "pts": 0, "fgm": 0, "fga": 0,
            "ast": 0
        })

        for game_id in game_ids:
            time.sleep(0.6)
            stats = process_4q_stats(game_id)
            for pid, statline in stats.items():
                for key in statline:
                    if key in ["name", "team"]:
                        combined_stats[pid][key] = statline[key]
                    else:
                        combined_stats[pid][key] += statline[key]

        points, fg, assists = aggregate_leaders(combined_stats)
        team_4q_diff = get_best_4q_team(date_str)
        tweet = compose_tweet(date_str, points, fg, assists, team_4q_diff)

        print("Tweeting:\n", tweet)
        client.create_tweet(text=tweet)

        update_clutch_json(date_str, points, fg, assists, team_4q_diff)

    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    run_bot()
