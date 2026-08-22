#!/usr/bin/env python3
"""Interactive Streamlit Economic Choices Dashboard for Florent Code League."""

import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path

# Page Configuration
st.set_page_config(
    page_title="Florent Top 10 Economic Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Database Connection Helper
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "economic_analysis.db"
RELEASE_DB_PATH = DATA_DIR / "dashboard_release.db"

if not DB_PATH.exists():
    if not RELEASE_DB_PATH.exists():
        import urllib.request
        import gzip
        import shutil
        import ssl
        
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Using a fixed tag 'latest-db' for the continuous release
        url = "https://github.com/slowTake/teaPot/releases/download/latest-db/dashboard_release.db.gz"
        gz_path = DATA_DIR / "dashboard_release.db.gz"
        
        print("Downloading database from GitHub Releases...")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        try:
            with urllib.request.urlopen(url, context=ctx) as response, open(gz_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
                
            print("Decompressing database...")
            with gzip.open(gz_path, 'rb') as f_in, open(RELEASE_DB_PATH, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
                
            gz_path.unlink()
            print("Database ready!")
        except Exception as e:
            print(f"Failed to download database: {e}")
            
    DB_PATH = RELEASE_DB_PATH

@st.cache_data(ttl=600)
def load_teams():
    # Cache bust 6
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT DISTINCT t.team_id, t.name, t.rank, t.rating
        FROM teams t
        JOIN game_economic_summaries s ON t.team_id = s.team_id
        ORDER BY t.rank ASC
    """, conn)
    conn.close()
    
    # Enrich with ladder API
    import subprocess, json
    try:
        res = subprocess.run(["fcode", "ladder", "--json"], capture_output=True, text=True, check=True)
        ladder = json.loads(res.stdout)
        team_meta = {t["teamName"]: {"region": t.get("region"), "studentStatus": t.get("studentStatus")} for t in ladder}
        df["region"] = df["name"].map(lambda x: team_meta.get(x, {}).get("region", "unknown"))
        df["studentStatus"] = df["name"].map(lambda x: team_meta.get(x, {}).get("studentStatus", "unknown"))
    except Exception as e:
        df["region"] = "unknown"
        df["studentStatus"] = "unknown"
        
    return df


@st.cache_data(ttl=600)
def load_game_summaries():
    # Cache bust 5
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT s.*, t.name as team_name, o.name as opponent_name, o.rating as opp_rating, g.turns_played, g.win_condition, m.created_at,
               (g.winner_team_id = s.team_id) as won
        FROM game_economic_summaries s
        JOIN teams t ON s.team_id = t.team_id
        JOIN teams o ON s.opponent_team_id = o.team_id
        JOIN games g ON s.game_id = g.game_id
        JOIN matches m ON g.match_id = m.match_id
    """, conn)
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_events(team_name=None, map_name=None):
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT e.*, t.name as team_name, o.name as opponent_name,
               CASE WHEN e.team_id = m.team_a_id THEN 'Team A (Top/Left Core)'
                    ELSE 'Team B (Bottom/Right Core)' END as side_played
        FROM titanium_events e
        JOIN teams t ON e.team_id = t.team_id
        JOIN teams o ON e.opponent_team_id = o.team_id
        JOIN games g ON e.game_id = g.game_id
        JOIN matches m ON g.match_id = m.match_id
        WHERE 1=1
    """
    params = []
    if team_name and team_name != "All Teams":
        query += " AND t.name = ?"
        params.append(team_name)
    if map_name and map_name != "All Maps":
        query += " AND e.map_name = ?"
        params.append(map_name)

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


# Main Application Layout
st.title("⚡ Florent Top 10 Economic Choice Analytics")
st.markdown("Interactive exploration of Titanium spending baselines, spatial deployment, and opponent-induced economic shifts across 24h of ranked matches.")

if not DB_PATH.exists():
    st.error(f"Database not found at `{DB_PATH}`. Run `python scripts/build_econ_db.py` first.")
    st.stop()

@st.cache_data(ttl=600)
def load_diagnostic_events():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("""
            SELECT d.*, t.name as team_name, g.map_name
            FROM diagnostic_events d
            JOIN teams t ON d.team_id = t.team_id
            JOIN games g ON d.game_id = g.game_id
        """, conn)
    except Exception:
        df = pd.DataFrame() # Return empty if table doesn't exist yet
    conn.close()
    return df

# Sidebar Navigation
st.sidebar.title("Navigation & Filters")
mode = st.sidebar.radio(
    "Select View Mode:",
    ["⚔️ Opponent Matchup Delta Engine", "🗺️ Map Analyzer", "🔍 Granular Spending Explorer", "🚨 Titanium Waste Analytics"]
)

st.sidebar.markdown("---")
min_sample = st.sidebar.number_input("Minimum Match Sample Size:", min_value=1, value=5, help="Hide teams/opponents with fewer matches than this to reduce noise.")

only_student = st.sidebar.checkbox("Only Student Teams", value=False)
only_nordic = st.sidebar.checkbox("Only Nordic Teams", value=False)

teams_df = load_teams()
summaries_df = load_game_summaries()

if only_student:
    valid_names = set(teams_df[teams_df["studentStatus"] == "student"]["name"])
    teams_df = teams_df[(teams_df["studentStatus"] == "student") | (teams_df["name"] == "HTTP 418")]
    summaries_df = summaries_df[summaries_df["opponent_name"].isin(valid_names)]

if only_nordic:
    valid_names = set(teams_df[teams_df["region"] == "nordic"]["name"])
    teams_df = teams_df[(teams_df["region"] == "nordic") | (teams_df["name"] == "HTTP 418")]
    summaries_df = summaries_df[summaries_df["opponent_name"].isin(valid_names)]

unfiltered_summaries_df = summaries_df.copy()

st.sidebar.markdown("---")
if "created_at" in summaries_df.columns:
    summaries_df["created_at"] = pd.to_datetime(summaries_df["created_at"]).dt.tz_localize(None)
    min_time = summaries_df["created_at"].min()
    max_time = summaries_df["created_at"].max()
    
    if pd.notnull(min_time) and pd.notnull(max_time) and min_time < max_time:
        min_dt = min_time.to_pydatetime()
        max_dt = max_time.to_pydatetime()
        
        time_range = st.sidebar.slider(
            "Filter Database by Time (UTC):",
            min_value=min_dt,
            max_value=max_dt,
            value=(min_dt, max_dt),
            format="MM/DD HH:mm"
        )
        summaries_df = summaries_df[(summaries_df["created_at"] >= time_range[0]) & (summaries_df["created_at"] <= time_range[1])]

valid_game_ids = set(summaries_df["game_id"].unique())

# -----------------------------------------------------------------------------
# MODE 3: OPPONENT MATCHUP DELTA ENGINE
# -----------------------------------------------------------------------------
if mode == "⚔️ Opponent Matchup Delta Engine":
    st.header("⚔️ Opponent-Induced Economic Shift Engine")
    st.markdown("Quantify how playing against a specific opponent forces a team to alter its Titanium spending compared to its baseline.")

    col_team, _ = st.columns([1, 2])
    with col_team:
        team_list = list(teams_df["name"].unique())
        default_index = team_list.index("HTTP 418") if "HTTP 418" in team_list else 0
        subject_team = st.selectbox("Select Subject Team:", team_list, index=default_index)

    if subject_team:
        st.subheader(f"Top 10 Ladder Matchups for {subject_team} (Min {int(min_sample)} Matches)")
        subject_rating = teams_df[teams_df["name"] == subject_team]["rating"].iloc[0]
        
        top_10_names = teams_df.sort_values("rank").head(10)["name"].tolist()
        all_opponents = teams_df[teams_df["name"] != subject_team].sort_values("rank")
        
        table_data = []
        for _, row in all_opponents.iterrows():
            if len(table_data) >= 10:
                break
                
            opp_name = row["name"]
            opp_rating = row["rating"]
            
            # Actual Winrate
            matchup_data = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["opponent_name"] == opp_name)]
            games_played = len(matchup_data)
            
            if games_played < min_sample:
                continue

            actual_wr = matchup_data["won"].mean() * 100
            
            # Get the set of maps played in this matchup
            maps_played = matchup_data["map_name"].unique()
            
            # Opponent's winrate on those maps against OTHER top 10 teams
            opp_overall = summaries_df[
                (summaries_df["team_name"] == opp_name) & 
                (summaries_df["map_name"].isin(maps_played)) &
                (summaries_df["opponent_name"].isin(top_10_names)) &
                (summaries_df["opponent_name"] != subject_team)
            ]
            
            if len(opp_overall) == 0:
                # Fallback to unfiltered all-time baseline on these maps
                opp_overall = unfiltered_summaries_df[
                    (unfiltered_summaries_df["team_name"] == opp_name) & 
                    (unfiltered_summaries_df["map_name"].isin(maps_played)) &
                    (unfiltered_summaries_df["opponent_name"].isin(top_10_names)) &
                    (unfiltered_summaries_df["opponent_name"] != subject_team)
                ]
            
            if len(opp_overall) == 0:
                # Absolute fallback to unfiltered all-time baseline across ALL maps
                opp_overall = unfiltered_summaries_df[
                    (unfiltered_summaries_df["team_name"] == opp_name) & 
                    (unfiltered_summaries_df["opponent_name"].isin(top_10_names)) &
                    (unfiltered_summaries_df["opponent_name"] != subject_team)
                ]

            if len(opp_overall) > 0:
                opp_winrate = opp_overall["won"].mean() * 100
                expected_wr = 100.0 - opp_winrate
                perf = actual_wr - expected_wr
                perf_str = f"{perf:+.1f}%"
                expected_str = f"{expected_wr:.1f}%"
            else:
                expected_str = "N/A"
                perf_str = "N/A"
                
            wins = matchup_data[matchup_data["won"] == True]
            losses = matchup_data[matchup_data["won"] == False]
            
            if len(wins) > 0:
                avg_win_turns = f"{wins['turns_played'].mean():.0f}"
                top_win_cond = wins['win_condition'].value_counts()
                top_win_pct = top_win_cond.iloc[0] / len(wins) * 100
                win_method_str = f"{top_win_cond.index[0]} ({top_win_pct:.0f}%)"
            else:
                avg_win_turns = "N/A"
                win_method_str = "N/A"

            if len(losses) > 0:
                avg_loss_turns = f"{losses['turns_played'].mean():.0f}"
                top_loss_cond = losses['win_condition'].value_counts()
                top_loss_pct = top_loss_cond.iloc[0] / len(losses) * 100
                loss_method_str = f"{top_loss_cond.index[0]} ({top_loss_pct:.0f}%)"
            else:
                avg_loss_turns = "N/A"
                loss_method_str = "N/A"
                
            table_data.append({
                "Opponent": opp_name,
                "Rank": row["rank"],
                "Matches": games_played,
                "E%": expected_str,
                "A%": f"{actual_wr:.1f}%",
                "Δ": perf_str,
                "Win Method": win_method_str,
                "Avg Win Turns": avg_win_turns,
                "Loss Method": loss_method_str,
                "Avg Loss Turns": avg_loss_turns
            })
            
        if len(table_data) > 0:
            st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)
        else:
            st.info(f"No opponents found with at least {int(min_sample)} matches.")
        st.divider()

        st.subheader("Individual Matchup Drilldown")
        col1, col2 = st.columns(2)
        with col1:
            opp_counts = summaries_df[summaries_df["team_name"] == subject_team]["opponent_name"].value_counts()
            opponents_available = opp_counts[opp_counts >= min_sample].index.tolist()
            selected_opponent = st.selectbox("Select Opponent Team:", opponents_available, index=0 if len(opponents_available) > 0 else None)
        with col2:
            if selected_opponent:
                maps_available = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["opponent_name"] == selected_opponent)]["map_name"].unique()
                selected_map = st.selectbox("Isolate Map (Optional):", ["All Maps"] + list(maps_available))
            else:
                selected_map = "All Maps"

    if subject_team and selected_opponent:
        # Check if pct_wasted is available
        has_waste = 'pct_wasted' in summaries_df.columns
        cols_to_mean = ["pct_builder", "pct_economy", "pct_defense", "pct_offense", "pct_own_core", "pct_opp_core", "won", "tles"]
        if has_waste:
            cols_to_mean.append("pct_wasted")

        # Calculate Baseline and Matchup
        if selected_map != "All Maps":
            base = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["map_name"] == selected_map)][cols_to_mean].mean()
            matchup = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["opponent_name"] == selected_opponent) & (summaries_df["map_name"] == selected_map)][cols_to_mean].mean()
        else:
            base = summaries_df[summaries_df["team_name"] == subject_team][cols_to_mean].mean()
            matchup = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["opponent_name"] == selected_opponent)][cols_to_mean].mean()

        if not matchup.empty:
            delta = matchup - base

            st.subheader(f"Economic Shift: {subject_team} vs {selected_opponent}")

            metric_cols = st.columns(7 if has_waste else 6)
            
            # Multiply winrate by 100 for percentage
            wr_base = base['won'] * 100
            wr_matchup = matchup['won'] * 100
            wr_delta = wr_matchup - wr_base
            
            tle_base = base['tles']
            tle_matchup = matchup['tles']
            tle_delta = tle_matchup - tle_base
            
            metric_cols[0].metric("Winrate", f"{wr_matchup:.1f}%", f"{wr_delta:+.1f}% vs base")
            metric_cols[1].metric("Avg TLEs/Game", f"{tle_matchup:.1f}", f"{tle_delta:+.1f} vs base", delta_color="inverse")
            metric_cols[2].metric("Defense Shift", f"{matchup['pct_defense']:.1f}%", f"{delta['pct_defense']:+.1f}% vs base")
            metric_cols[3].metric("Offense Shift", f"{matchup['pct_offense']:.1f}%", f"{delta['pct_offense']:+.1f}% vs base")
            metric_cols[4].metric("Harvester Shift", f"{matchup['pct_economy']:.1f}%", f"{delta['pct_economy']:+.1f}% vs base")
            metric_cols[5].metric("Forward Aggression Shift", f"{matchup['pct_opp_core']:.1f}%", f"{delta['pct_opp_core']:+.1f}% vs base")
            
            if has_waste:
                metric_cols[6].metric("Waste Shift", f"{matchup['pct_wasted']:.1f}%", f"{delta['pct_wasted']:+.1f}% vs base", delta_color="inverse")



            # Replay Launcher Module
            st.divider()
            st.subheader("🎬 Matchup Replay Vault")
            st.markdown("Launch the official web viewer to review specific games between these two teams.")
            
            # Fetch games between these two teams
            conn = sqlite3.connect(DB_PATH)
            if selected_map != "All Maps":
                games_df = pd.read_sql_query("""
                    SELECT g.game_id, g.match_id, g.game_number, g.map_name, s.pct_wasted, g.turns_played, g.replay_key,
                           CASE WHEN g.winner_team_id = s.team_id THEN 'WIN' ELSE 'LOSS' END as outcome,
                           s.tles as tle_count, s.tle_turns
                    FROM games g
                    JOIN game_economic_summaries s ON g.game_id = s.game_id
                    JOIN teams t ON s.team_id = t.team_id
                    JOIN teams o ON s.opponent_team_id = o.team_id
                    WHERE t.name = ? AND o.name = ? AND g.map_name = ?
                """, conn, params=(subject_team, selected_opponent, selected_map))
            else:
                games_df = pd.read_sql_query("""
                    SELECT g.game_id, g.match_id, g.game_number, g.map_name, s.pct_wasted, g.turns_played, g.replay_key,
                           CASE WHEN g.winner_team_id = s.team_id THEN 'WIN' ELSE 'LOSS' END as outcome,
                           s.tles as tle_count, s.tle_turns
                    FROM games g
                    JOIN game_economic_summaries s ON g.game_id = s.game_id
                    JOIN teams t ON s.team_id = t.team_id
                    JOIN teams o ON s.opponent_team_id = o.team_id
                    WHERE t.name = ? AND o.name = ?
                """, conn, params=(subject_team, selected_opponent))
            conn.close()

            if not games_df.empty:
                sort_col, _ = st.columns([1, 1])
                with sort_col:
                    sort_by = st.selectbox("Sort Replays By:", [
                        "Default", 
                        "Most TLEs First", 
                        "Match Length (Longest First)", 
                        "Match Length (Shortest First)"
                    ])
                
                if sort_by == "Most TLEs First":
                    games_df = games_df.sort_values(by="tle_count", ascending=False)
                elif sort_by == "Match Length (Longest First)":
                    games_df = games_df.sort_values(by="turns_played", ascending=False)
                elif sort_by == "Match Length (Shortest First)":
                    games_df = games_df.sort_values(by="turns_played", ascending=True)

                # Format a display string for the dropdown
                games_df["display"] = games_df.apply(
                    lambda row: f"{row['map_name']} | Result: {row['outcome']} | Waste: {row['pct_wasted']}% | Turns: {row['turns_played']} | TLEs: {row['tle_count']} | ID: {row['game_id'][:8]}", axis=1
                )
                
                selected_game_display = st.selectbox("Select Game to Launch:", games_df["display"])
                selected_row = games_df[games_df["display"] == selected_game_display].iloc[0]
                
                match_id = selected_row['match_id']
                game_num = selected_row['game_number']
                viewer_url = f"https://game.code.florent.vc/visualiser?matchId={match_id}&game={game_num}"
                st.markdown(f"**[▶️ Launch in Official Web Viewer]({viewer_url})**")
                
                if selected_row['tle_count'] > 0:
                    if pd.notnull(selected_row['tle_turns']) and selected_row['tle_turns']:
                        st.info(f"**TLE Analysis**: Your bot stalled on Turn(s) {selected_row['tle_turns']}.")
                    else:
                        st.info("**TLE Analysis**: Your bot stalled during this match, but exact turns were not recorded (requires database rebuild).")

            else:
                st.info("No games found for this specific matchup in the database.")

# -----------------------------------------------------------------------------
# MODE: MAP ANALYZER
# -----------------------------------------------------------------------------
elif mode == "🗺️ Map Analyzer":
    st.header("🗺️ Map Analyzer: Opponent Variation")
    st.markdown("Keep the map constant and observe how your strategy deviates against specific subsets of opponents compared to your baseline on this map.")

    col1, col2 = st.columns(2)
    with col1:
        team_list = list(teams_df["name"].unique())
        default_index = team_list.index("HTTP 418") if "HTTP 418" in team_list else 0
        subject_team = st.selectbox("Select Subject Team:", team_list, index=default_index, key="ma_team")
    with col2:
        maps_available = summaries_df[summaries_df["team_name"] == subject_team]["map_name"].unique()
        selected_map = st.selectbox("Isolate Map (Optional Drilldown):", maps_available, key="ma_map")

    if subject_team:
        subject_data = summaries_df[summaries_df["team_name"] == subject_team]
        map_table = []
        for m in maps_available:
            map_df = subject_data[subject_data["map_name"] == m]
            games = len(map_df)
            if games == 0: continue
            
            wr = map_df["won"].mean() * 100
            tles = map_df["tles"].mean()
            wins = map_df[map_df["won"] == True]
            losses = map_df[map_df["won"] == False]
            
            avg_win_turns = float(wins['turns_played'].mean()) if len(wins) > 0 else None
            avg_loss_turns = float(losses['turns_played'].mean()) if len(losses) > 0 else None
            
            map_table.append({
                "Map": m,
                "Matches": games,
                "Winrate": float(wr),
                "Avg Win Length": avg_win_turns,
                "Avg Loss Length": avg_loss_turns,
                "Avg TLEs": float(tles)
            })
            
        st.subheader(f"Overall Map Performance for {subject_team}")
        if len(map_table) > 0:
            df_maps = pd.DataFrame(map_table).sort_values("Matches", ascending=False)
            st.dataframe(
                df_maps,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Winrate": st.column_config.NumberColumn("Winrate", format="%.1f%%"),
                    "Avg Win Length": st.column_config.NumberColumn("Avg Win Length", format="%.0f"),
                    "Avg Loss Length": st.column_config.NumberColumn("Avg Loss Length", format="%.0f"),
                    "Avg TLEs": st.column_config.NumberColumn("Avg TLEs", format="%.1f")
                }
            )
        else:
            st.info("No map data available.")
        st.divider()

    if subject_team and selected_map:
        opp_map_counts = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["map_name"] == selected_map)]["opponent_name"].value_counts()
        opponents_on_map = opp_map_counts.index.tolist()
        
        # Create a dictionary to map opponent names to their ratings for the UI
        opp_rating_dict = summaries_df[summaries_df["team_name"] == subject_team].set_index("opponent_name")["opp_rating"].to_dict()
        format_opp = lambda opp: f"{opp} (Rating: {opp_rating_dict.get(opp, 0):.0f}) - {opp_map_counts.get(opp, 0)} matches"
        
        all_opponents = st.checkbox("Select All Opponents", value=True, key="ma_all_opps")
        if all_opponents:
            selected_opponents = st.multiselect("Select Opponents to Aggregate:", opponents_on_map, default=opponents_on_map, format_func=format_opp, key="ma_opps")
        else:
            selected_opponents = st.multiselect("Select Opponents to Aggregate:", opponents_on_map, default=[], format_func=format_opp, key="ma_opps")

        if selected_opponents:
            base_df = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["map_name"] == selected_map)]
            matchup_df = summaries_df[(summaries_df["team_name"] == subject_team) & (summaries_df["map_name"] == selected_map) & (summaries_df["opponent_name"].isin(selected_opponents))]

            if not matchup_df.empty:
                st.subheader(f"Performance on {selected_map}")

                metric_cols = st.columns(4)
                
                # Winrate
                wr_base = base_df['won'].mean() * 100
                wr_matchup = matchup_df['won'].mean() * 100
                wr_delta = wr_matchup - wr_base
                
                # TLEs
                tle_base = base_df['tles'].mean()
                tle_matchup = matchup_df['tles'].mean()
                tle_delta = tle_matchup - tle_base
                
                # Avg Length Wins
                base_wins = base_df[base_df['won'] == True]
                match_wins = matchup_df[matchup_df['won'] == True]
                awin_base = base_wins['turns_played'].mean() if len(base_wins) > 0 else 0
                awin_match = match_wins['turns_played'].mean() if len(match_wins) > 0 else 0
                awin_delta = awin_match - awin_base

                # Avg Length Losses
                base_losses = base_df[base_df['won'] == False]
                match_losses = matchup_df[matchup_df['won'] == False]
                aloss_base = base_losses['turns_played'].mean() if len(base_losses) > 0 else 0
                aloss_match = match_losses['turns_played'].mean() if len(match_losses) > 0 else 0
                aloss_delta = aloss_match - aloss_base
                
                metric_cols[0].metric("Winrate", f"{wr_matchup:.1f}%", f"{wr_delta:+.1f}% vs base")
                metric_cols[1].metric("Avg TLEs/Game", f"{tle_matchup:.1f}", f"{tle_delta:+.1f} vs base", delta_color="inverse")
                
                if len(match_wins) > 0:
                    metric_cols[2].metric("Avg Length (Wins)", f"{awin_match:.0f} turns", f"{awin_delta:+.0f} vs base", delta_color="inverse")
                else:
                    metric_cols[2].metric("Avg Length (Wins)", "N/A", "N/A")

                if len(match_losses) > 0:
                    metric_cols[3].metric("Avg Length (Losses)", f"{aloss_match:.0f} turns", f"{aloss_delta:+.0f} vs base", delta_color="inverse")
                else:
                    metric_cols[3].metric("Avg Length (Losses)", "N/A", "N/A")

                # Replay Launcher Module
                st.divider()
                st.subheader("🎬 Map Analyzer Replay Vault")
                st.markdown("Launch the official web viewer to review specific games between these teams on this map.")

                conn = sqlite3.connect(DB_PATH)
                placeholders = ', '.join(['?'] * len(selected_opponents))
                query = f"""
                    SELECT g.game_id, g.match_id, g.game_number, g.map_name, s.pct_wasted, g.turns_played, g.replay_key,
                           o.name as opp_name,
                           CASE WHEN g.winner_team_id = s.team_id THEN 'WIN' ELSE 'LOSS' END as outcome,
                           s.tles as tle_count, s.tle_turns
                    FROM games g
                    JOIN game_economic_summaries s ON g.game_id = s.game_id
                    JOIN teams t ON s.team_id = t.team_id
                    JOIN teams o ON s.opponent_team_id = o.team_id
                    WHERE t.name = ? AND g.map_name = ? AND o.name IN ({placeholders})
                """
                params = [subject_team, selected_map] + selected_opponents
                games_df = pd.read_sql_query(query, conn, params=params)
                conn.close()

                if not games_df.empty:
                    sort_col, _ = st.columns([1, 1])
                    with sort_col:
                        sort_by = st.selectbox("Sort Replays By:", [
                            "Default", 
                            "Most TLEs First", 
                            "Match Length (Longest First)", 
                            "Match Length (Shortest First)"
                        ], key="ma_sort")
                    
                    if sort_by == "Most TLEs First":
                        games_df = games_df.sort_values(by="tle_count", ascending=False)
                    elif sort_by == "Match Length (Longest First)":
                        games_df = games_df.sort_values(by="turns_played", ascending=False)
                    elif sort_by == "Match Length (Shortest First)":
                        games_df = games_df.sort_values(by="turns_played", ascending=True)

                    games_df["display"] = games_df.apply(
                        lambda row: f"Vs {row['opp_name']} | Result: {row['outcome']} | Waste: {row['pct_wasted']}% | Turns: {row['turns_played']} | TLEs: {row['tle_count']} | ID: {row['game_id'][:8]}", axis=1
                    )

                    selected_game_display = st.selectbox("Select Game to Launch:", games_df["display"], key="ma_replay")

                    if selected_game_display:
                        selected_row = games_df[games_df["display"] == selected_game_display].iloc[0]

                        match_id = selected_row['match_id']
                        game_num = selected_row['game_number']
                        viewer_url = f"https://game.code.florent.vc/visualiser?matchId={match_id}&game={game_num}"
                        st.markdown(f"**[▶️ Launch in Official Web Viewer]({viewer_url})**")
                        
                        if selected_row['tle_count'] > 0:
                            if pd.notnull(selected_row['tle_turns']) and selected_row['tle_turns']:
                                st.info(f"**TLE Analysis**: Your bot stalled on Turn(s) {selected_row['tle_turns']}.")
                            else:
                                st.info("**TLE Analysis**: Your bot stalled during this match, but exact turns were not recorded (requires database rebuild).")
                else:
                    st.info("No games found matching these filters.")


# -----------------------------------------------------------------------------
# MODE 1: TITANIUM WASTE ANALYTICS
# -----------------------------------------------------------------------------
elif mode == "🚨 Titanium Waste Analytics":
    st.header("🚨 Titanium Waste Analytics")
    st.markdown("Analyze estimated Titanium loss due to inefficiencies like orphaned harvesters, idling, and rebuild churn.")

    events_df = load_diagnostic_events()
    if not events_df.empty:
        events_df = events_df[events_df["game_id"].isin(valid_game_ids)]

    if 'pct_wasted' in summaries_df.columns:
        st.subheader("Waste Efficiency Leaderboard")
        
        team_counts = summaries_df["team_name"].value_counts()
        valid_teams = team_counts[team_counts >= min_sample].index
        filtered_summaries = summaries_df[summaries_df["team_name"].isin(valid_teams)]
        
        # Calculate MAP-ADJUSTED waste per turn for each team to remove sample bias
        team_map_waste = filtered_summaries.groupby(["team_name", "map_name"])[["total_wasted_ti", "turns_played"]].sum().reset_index()
        team_map_waste["map_waste_per_turn"] = team_map_waste["total_wasted_ti"] / team_map_waste["turns_played"]
        
        waste_avg = team_map_waste.groupby("team_name")["map_waste_per_turn"].mean().reset_index()
        waste_avg.rename(columns={"map_waste_per_turn": "waste_per_turn"}, inplace=True)
        waste_avg = waste_avg.merge(teams_df[["name", "rank", "rating"]], left_on="team_name", right_on="name").sort_values("waste_per_turn", ascending=False)
        
        # Filter out invalid teams (like banned or missing teams) that have 0 rating so they don't skew the color scale
        waste_avg = waste_avg[waste_avg["rating"] > 0]
        
        # Calculate a continuous Viridis scale manually mapped to rating, but override HTTP 418
        from plotly.colors import sample_colorscale
        import numpy as np
        
        min_r = waste_avg["rating"].min()
        max_r = waste_avg["rating"].max()
        normalized_r = (waste_avg["rating"] - min_r) / (max_r - min_r + 1e-9)
        viridis_scale = sample_colorscale("Viridis", normalized_r)
        
        # Highlight our team in bright red
        colors = ["#ff0000" if team == "HTTP 418" else color for team, color in zip(waste_avg["team_name"], viridis_scale)]

        fig_waste = px.bar(
            waste_avg,
            x="team_name",
            y="waste_per_turn",
            hover_data=["rating"],
            title="Map-Adjusted Average Titanium Wasted Per Turn (Colored by Elo Rating)",
            labels={"waste_per_turn": "Waste (Ti/turn)", "team_name": "Team", "rating": "Elo Rating"}
        )
        fig_waste.update_traces(marker_color=colors)
        
        # Add a custom colorbar to explain the Viridis scale
        fig_waste.update_layout(
            xaxis_tickangle=-45,
            coloraxis=dict(colorscale="Viridis", cmin=min_r, cmax=max_r, colorbar_title="Elo Rating")
        )
        
        # To get the colorbar to actually show up when overriding marker_color, we have to inject a dummy trace or map it.
        # It's cleaner to just let the hover_data show the rating.
        st.plotly_chart(fig_waste, use_container_width=True)
    else:
        st.warning("Waste columns not found in game_economic_summaries. Ensure data enrichment has run.")

    if not events_df.empty:
        import math
        from plotly.subplots import make_subplots
        import plotly.graph_objects as go
        
        st.subheader("Waste Profile: Map Grid vs Baseline (Waste per Turn)")
        
        team_list = list(teams_df["name"].unique())
        default_index = team_list.index("HTTP 418") if "HTTP 418" in team_list else 0
        selected_team_waste = st.selectbox("Select Team for Waste Breakdown:", team_list, index=default_index)
        
        team_waste_df = events_df[events_df["team_name"] == selected_team_waste]
        
        if not team_waste_df.empty:
            team_summaries = summaries_df[summaries_df["team_name"] == selected_team_waste]
            total_turns_baseline = team_summaries["turns_played"].sum()
            
            all_detectors = pd.DataFrame({"detector": team_waste_df["detector"].unique()})
            
            # Baseline (Waste per Turn)
            baseline = all_detectors.merge(team_waste_df.groupby("detector")["estimated_ti_loss"].sum().reset_index(), on="detector", how="left").fillna(0)
            baseline["waste_per_turn"] = (baseline["estimated_ti_loss"] / total_turns_baseline) if total_turns_baseline > 0 else 0
            
            r_base = baseline["waste_per_turn"].tolist()
            r_base.append(r_base[0]) # close loop
            theta_base = baseline["detector"].str.replace("Improved", "").str.replace("Detector", "").tolist()
            theta_base.append(theta_base[0])
            
            maps_list = sorted(list(team_waste_df["map_name"].unique()))
            cols = 5
            rows = math.ceil(len(maps_list) / cols) if maps_list else 1
            
            specs = [[{'type': 'polar'} for _ in range(cols)] for _ in range(rows)]
            fig_grid = make_subplots(rows=rows, cols=cols, specs=specs, subplot_titles=maps_list, vertical_spacing=0.08, horizontal_spacing=0.05)
            
            # To normalize radial axis across all subplots, find the max waste_per_turn across all maps
            max_waste = baseline["waste_per_turn"].max()
            
            map_stats = []
            for i, map_name in enumerate(maps_list):
                row = (i // cols) + 1
                col = (i % cols) + 1
                
                total_turns_map = team_summaries[team_summaries["map_name"] == map_name]["turns_played"].sum()
                map_df = team_waste_df[team_waste_df["map_name"] == map_name]
                map_waste = all_detectors.merge(map_df.groupby("detector")["estimated_ti_loss"].sum().reset_index(), on="detector", how="left").fillna(0)
                map_waste["waste_per_turn"] = (map_waste["estimated_ti_loss"] / total_turns_map) if total_turns_map > 0 else 0
                
                # Collect stats for the summary table
                for _, r_data in map_waste.iterrows():
                    map_stats.append({
                        "map_name": map_name,
                        "detector": r_data["detector"],
                        "waste_per_turn": r_data["waste_per_turn"]
                    })
                
                r_map = map_waste["waste_per_turn"].tolist()
                r_map.append(r_map[0])
                
                if max(r_map) > max_waste:
                    max_waste = max(r_map)
                
                # Map Trace (Crimson)
                fig_grid.add_trace(go.Scatterpolar(
                    r=r_map, theta=theta_base, fill='toself', name='Map Waste',
                    line=dict(color='crimson'), fillcolor='rgba(220,20,60,0.5)',
                    showlegend=False, hoverinfo='text', text=[f"{val:.2f} Ti/turn" for val in r_map]
                ), row=row, col=col)
                
            fig_grid.update_layout(height=max(300, rows*250), margin=dict(t=50, b=50, l=50, r=50))
            
            # Normalize radial axis and format polar subplots
            polar_layout = dict(
                radialaxis=dict(visible=False, range=[0, max_waste]),
                angularaxis=dict(tickfont=dict(size=8), direction="clockwise")
            )
            
            # Update all polar axes dynamically
            for i in range(1, len(maps_list) + 1):
                polar_key = f"polar{i}" if i > 1 else "polar"
                fig_grid.update_layout(**{polar_key: polar_layout})
                
            st.plotly_chart(fig_grid, use_container_width=True)
            
            # --- Summary Table ---
            st.subheader("Top 3 Waste Contributors: Map Extremes")
            map_stats_df = pd.DataFrame(map_stats)
            top_3 = baseline.sort_values("estimated_ti_loss", ascending=False).head(3)
            
            summary_rows = []
            for _, r_data in top_3.iterrows():
                cat = r_data["detector"]
                base_val = r_data["waste_per_turn"]
                clean_cat = cat.replace("Improved", "").replace("Detector", "")
                
                cat_df = map_stats_df[map_stats_df["detector"] == cat].copy()
                cat_df["delta"] = cat_df["waste_per_turn"] - base_val
                
                worst = cat_df.sort_values("delta", ascending=False).head(3)
                best = cat_df.sort_values("delta", ascending=True).head(3)
                
                worst_str = ", ".join([f"{m} (+{d:.2f})" for m, d in zip(worst["map_name"], worst["delta"])])
                best_str = ", ".join([f"{m} ({d:.2f})" for m, d in zip(best["map_name"], best["delta"])])
                
                summary_rows.append({
                    "Waste Category": clean_cat, 
                    "Worst Maps (vs Baseline)": worst_str, 
                    "Best Maps (vs Baseline)": best_str
                })
                
            st.table(pd.DataFrame(summary_rows))
            
            st.subheader(f"Opponent Waste Disruption: How {selected_team_waste} Affects Enemies")
            
            # We are looking at rows where the 'team_name' is the opponent, and 'opponent_name' is us
            vs_us_df = summaries_df[summaries_df["opponent_name"] == selected_team_waste]
            vs_others_df = summaries_df[summaries_df["opponent_name"] != selected_team_waste]
            
            opponents_played = vs_us_df["team_name"].unique()
            
            disruption_data = []
            for opp in opponents_played:
                # Opponent's games against us
                opp_vs_us = vs_us_df[vs_us_df["team_name"] == opp]
                # Opponent's games against others
                opp_vs_others = vs_others_df[vs_others_df["team_name"] == opp]
                
                # Global fallback just in case they haven't played a map against anyone else
                turns_others_global = opp_vs_others["turns_played"].sum()
                waste_others_global = opp_vs_others["total_wasted_ti"].sum()
                global_base_wpt = (waste_others_global / turns_others_global) if turns_others_global > 0 else 0
                
                maps_played = opp_vs_us["map_name"].unique()
                map_deltas = []
                
                for m in maps_played:
                    # Map vs others
                    m_others = opp_vs_others[opp_vs_others["map_name"] == m]
                    m_turns_o = m_others["turns_played"].sum()
                    m_waste_o = m_others["total_wasted_ti"].sum()
                    
                    if m_turns_o > 0:
                        m_base_wpt = m_waste_o / m_turns_o
                    else:
                        m_base_wpt = global_base_wpt
                        
                    # Map vs us
                    m_us = opp_vs_us[opp_vs_us["map_name"] == m]
                    m_turns_u = m_us["turns_played"].sum()
                    m_waste_u = m_us["total_wasted_ti"].sum()
                    m_us_wpt = (m_waste_u / m_turns_u) if m_turns_u > 0 else 0
                    
                    map_deltas.append({"map": m, "delta": m_us_wpt - m_base_wpt})
                    
                # The map-adjusted overall delta is the average of the per-map deltas
                overall_delta = sum(d["delta"] for d in map_deltas) / len(map_deltas) if map_deltas else 0
                
                map_deltas.sort(key=lambda x: x["delta"])
                
                best_maps = map_deltas[:3] # We least increase their waste
                worst_maps = map_deltas[-3:] # We most increase their waste
                worst_maps.reverse() # Sort descending
                
                best_str = ", ".join([f"{x['map']} ({x['delta']:+.2f})" for x in best_maps])
                worst_str = ", ".join([f"{x['map']} ({x['delta']:+.2f})" for x in worst_maps])
                
                disruption_data.append({
                    "Opponent": opp,
                    "Avg Waste Increase (Map-Adjusted)": f"{overall_delta:+.2f} Ti/turn",
                    "Highest Disruption Maps (Increase)": worst_str,
                    "Lowest Disruption Maps (Decrease)": best_str,
                    "overall_val": overall_delta
                })
                
            if disruption_data:
                disruption_df = pd.DataFrame(disruption_data).sort_values("overall_val", ascending=False)
                disruption_df = disruption_df.drop(columns=["overall_val"])
                st.table(disruption_df)
            else:
                st.info(f"Not enough data to calculate opponent disruption for {selected_team_waste}.")
        else:
            st.info(f"No waste events recorded for {selected_team_waste}.")
    else:
        st.info("No diagnostic events found in the database. Ensure data enrichment has run.")
elif mode == "🔍 Granular Spending Explorer":
    st.header("🔍 Granular Spending Explorer")
    st.markdown("Analyze highly specific Titanium expenditures based on exact distance to cores.")

    col1, col2, col3 = st.columns(3)
    with col1:
        team_counts = summaries_df["team_name"].value_counts()
        valid_teams = team_counts[team_counts >= min_sample].index.tolist()
        team_list = valid_teams if valid_teams else list(teams_df["name"].unique())
        default_index = team_list.index("HTTP 418") if "HTTP 418" in team_list else 0
        subject_team = st.selectbox("Select Team:", team_list, index=default_index, key="gse_team")
    with col2:
        maps_available = ["All Maps"] + list(summaries_df["map_name"].unique())
        selected_map = st.selectbox("Select Map:", maps_available, key="gse_map")
    with col3:
        reference_point = st.selectbox("Reference Point:", ["dist_to_own_core", "dist_to_opp_core"])
        
    col4, col5 = st.columns(2)
    with col4:
        operator = st.selectbox("Distance Operator:", ["<=", ">=", "==", "<", ">"])
    with col5:
        distance_limit = st.number_input("Distance Threshold:", min_value=0, max_value=100, value=5)
        
    if subject_team:
        conn = sqlite3.connect(DB_PATH)
        
        # Verify the schema has been updated
        try:
            has_cols = conn.execute("SELECT dist_to_own_core FROM titanium_events LIMIT 1")
            
            map_filter = ""
            params = [subject_team, distance_limit]
            if selected_map != "All Maps":
                map_filter = "AND e.map_name = ?"
                params.insert(1, selected_map)

            if valid_game_ids:
                # SQLite can handle large IN clauses
                game_filter = "AND e.game_id IN ('" + "','".join(valid_game_ids) + "')"
            else:
                game_filter = "AND 1=0"

            query = f"""
                SELECT e.action_type, SUM(e.titanium_cost) as total_spent
                FROM titanium_events e
                JOIN teams t ON e.team_id = t.team_id
                WHERE t.name = ? 
                {map_filter}
                {game_filter}
                AND e.{reference_point} {operator} ?

                GROUP BY e.action_type
                ORDER BY total_spent DESC
            """
            
            results_df = pd.read_sql_query(query, conn, params=params)
            
            if not results_df.empty:
                st.subheader(f"Spending where {reference_point} {operator} {distance_limit}")
                
                fig = px.pie(
                    results_df, 
                    names="action_type", 
                    values="total_spent", 
                    title=f"Titanium Spending Distribution",
                    hole=0.4
                )
                fig.update_traces(textinfo='percent+label')
                st.plotly_chart(fig, use_container_width=True)
                
                st.dataframe(results_df.rename(columns={"action_type": "Action Type", "total_spent": "Total Titanium Spent"}), use_container_width=True)
            else:
                st.info("No spending found matching these exact criteria.")
                
        except sqlite3.OperationalError:
            st.error("⚠️ The `titanium_events` table does not have the new distance columns yet. You must rebuild the database first using `python3 scratch/build_recent_db.py --skip-enrichment`.")
            
        conn.close()
