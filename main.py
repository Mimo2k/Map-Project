import streamlit as st
import sqlite3
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.distance import geodesic
from streamlit_autorefresh import st_autorefresh
import random
from datetime import datetime

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('geo_extreme.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, total_score REAL)')
    # Added boundary_km to sessions
    c.execute('''CREATE TABLE IF NOT EXISTS sessions 
                 (session_name TEXT PRIMARY KEY, creator TEXT, target_lat REAL, target_long REAL, 
                  boundary_km REAL, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS progress 
                 (session_name TEXT, username TEXT, current_lat REAL, current_long REAL, 
                  distance_to_go REAL, last_updated TEXT, PRIMARY KEY(session_name, username))''')
    conn.commit()
    return conn

conn = init_db()

# --- REFRESH LOGIC ---
# This forces the app to rerun every 5 seconds to get "Live" updates
st_autorefresh(interval=5000, key="datarefresh")

# --- HELPER: GENERATE NEW LOCATION ---
def generate_new_target(session_name, center_lat, center_lon, radius_km):
    # Roughly 1 degree ~= 111km
    offset = radius_km / 111.0
    new_lat = center_lat + random.uniform(-offset, offset)
    new_long = center_lon + random.uniform(-offset, offset)
    conn.execute('UPDATE sessions SET target_lat=?, target_long=? WHERE session_name=?', 
                 (new_lat, new_long, session_name))
    conn.commit()
    # Clear previous progress for the new round
    conn.execute('DELETE FROM progress WHERE session_name=?', (session_name,))
    conn.commit()

# --- APP SETUP ---
st.set_page_config(page_title="Mimo Geo-Quest AI", layout="wide")

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

# --- SIDEBAR: AUTH ---
with st.sidebar:
    st.title("📍 Geo-Quest Pro")
    if not st.session_state.logged_in:
        u = st.text_input("User")
        p = st.text_input("Pass", type="password")
        if st.button("Login"):
            res = conn.execute('SELECT * FROM users WHERE username=? AND password=?', (u, p)).fetchone()
            if res:
                st.session_state.logged_in = True
                st.session_state.username = u
                st.rerun()
        if st.button("Sign Up"):
            try:
                conn.execute('INSERT INTO users VALUES (?,?,0)', (u, p))
                conn.commit()
                st.success("User Created")
            except: st.error("Error")
    else:
        st.write(f"Logged in as: **{st.session_state.username}**")
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.rerun()

# --- MAIN LOGIC ---
if st.session_state.logged_in:
    tab1, tab2, tab3, tab4 = st.tabs(["Lobby", "Mission Control", "Leaderboard", "🛡️ Moderator"])

    with tab1:
        st.subheader("Servers")
        col_c, col_j = st.columns(2)
        with col_c:
            s_name = st.text_input("Server Name")
            s_bound = st.number_input("Boundary (KM from current loc)", min_value=1.0, value=5.0)
            if st.button("Host New Game"):
                # Initial target is 0,0 - will be updated once moderator gets location
                conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)', 
                             (s_name, st.session_state.username, 23.23, 87.07, s_bound, "ACTIVE"))
                conn.commit()
                st.success("Server Created! Go to Moderator tab to set first target.")
        
        with col_j:
            active_s = pd.read_sql_query("SELECT session_name, creator, boundary_km FROM sessions WHERE status='ACTIVE'", conn)
            st.dataframe(active_s, hide_index=True)
            to_join = st.selectbox("Select Server", active_s['session_name'].tolist() if not active_s.empty else [])
            if st.button("Join Server"):
                st.session_state.current_session = to_join

    # --- GEOLOCATION COMPONENT ---
    # We use a simple text-based manual override + JS Bridge
    js_geo = """
    <script>
    navigator.geolocation.getCurrentPosition((pos) => {
        window.parent.postMessage({
            type: 'streamlit:set_component_value',
            value: {lat: pos.coords.latitude, lon: pos.coords.longitude}
        }, '*');
    });
    </script>
    """
    st.components.v1.html(js_geo, height=0)
    
    # MISSION TAB
    with tab2:
        if "current_session" in st.session_state:
            s_data = conn.execute('SELECT * FROM sessions WHERE session_name=?', (st.session_state.current_session,)).fetchone()
            target = (s_data[2], s_data[3])
            
            # Use data from JS or fallback to manual
            loc_data = st.session_state.get("location_data")
            if loc_data:
                u_lat, u_lon = loc_data['lat'], loc_data['lon']
            else:
                st.warning("Waiting for GPS... or enter manually:")
                u_lat = st.number_input("Lat", value=23.2300, key="mlat")
                u_lon = st.number_input("Lon", value=87.0700, key="mlon")
            
            user_coords = (u_lat, u_lon)
            dist = geodesic(user_coords, target).meters
            
            # Update position in DB for others to see
            conn.execute('INSERT OR REPLACE INTO progress VALUES (?,?,?,?,?,?)',
                         (st.session_state.current_session, st.session_state.username, u_lat, u_lon, dist, datetime.now().isoformat()))
            conn.commit()

            # MAP VISUALS
            m = folium.Map(location=user_coords, zoom_start=15)
            # Shortest Path (Line)
            folium.PolyLine([user_coords, target], color="blue", weight=2.5, opacity=1, tooltip="Shortest Path").add_to(m)
            # User Marker
            folium.Marker(user_coords, popup="You", icon=folium.Icon(color='blue', icon='user')).add_to(m)
            # Target Marker
            folium.Marker(target, popup="Goal", icon=folium.Icon(color='red', icon='flag')).add_to(m)
            # 50m WIN ZONE
            folium.Circle(radius=50, location=target, color="green", fill=True, fill_opacity=0.3).add_to(m)
            
            st_folium(m, width="100%", height=500, key="game_map")

            # WINNING CONDITION
            if dist <= 50:
                st.balloons()
                st.success("🎉 MISSION ACCOMPLISHED! You reached the 50m zone.")
                # Update Score
                conn.execute('UPDATE users SET total_score = total_score + 100 WHERE username=?', (st.session_state.username,))
                # Trigger New Location for the session
                generate_new_target(st.session_state.current_session, target[0], target[1], s_data[4])
                st.rerun()

            # Live Opponents List
            st.write("### 🏃 LIVE TRACKER")
            opps = pd.read_sql_query("SELECT username, distance_to_go FROM progress WHERE session_name=? ORDER BY distance_to_go ASC", 
                                    conn, params=(st.session_state.current_session,))
            st.table(opps)
        else:
            st.info("Join a server in the Lobby")

    # LEADERBOARD TAB
    with tab3:
        st.subheader("Global Leaderboard")
        board = pd.read_sql_query("SELECT username, total_score FROM users ORDER BY total_score DESC", conn)
        st.dataframe(board, use_container_width=True, hide_index=True)

    # MODERATOR TAB
    with tab4:
        if "current_session" in st.session_state:
            s_data = conn.execute('SELECT * FROM sessions WHERE session_name=?', (st.session_state.current_session,)).fetchone()
            if s_data[1] == st.session_state.username:
                st.success("🛡️ You are the Moderator of this Session")
                
                col1, col2 = st.columns(2)
                with col1:
                    new_bound = st.slider("Adjust Boundary (KM)", 1, 50, int(s_data[4]))
                    if st.button("Update Boundary & Generate New Target"):
                        generate_new_target(st.session_state.current_session, s_data[2], s_data[3], new_bound)
                        conn.execute('UPDATE sessions SET boundary_km=? WHERE session_name=?', (new_bound, st.session_state.current_session))
                        conn.commit()
                        st.rerun()
                
                with col2:
                    if st.button("🛑 End Current Game"):
                        conn.execute('UPDATE sessions SET status="ENDED" WHERE session_name=?', (st.session_state.current_session,))
                        conn.commit()
                        st.warning("Game Ended")
                        st.rerun()
            else:
                st.error("You are not the moderator of this session.")
        else:
            st.info("Join your own session to see moderator controls.")