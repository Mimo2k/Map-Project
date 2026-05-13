import streamlit as st
import sqlite3
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.distance import geodesic
import random
from datetime import datetime

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('geo_race.db', check_same_thread=False)
    c = conn.cursor()
    # Users: Credentials and overall score
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, total_score REAL)''')
    # Sessions: Custom named servers with target locations
    c.execute('''CREATE TABLE IF NOT EXISTS sessions 
                 (session_name TEXT PRIMARY KEY, creator TEXT, target_lat REAL, target_long REAL, status TEXT)''')
    # Progress: Live tracking of players within sessions
    c.execute('''CREATE TABLE IF NOT EXISTS progress 
                 (session_name TEXT, username TEXT, current_lat REAL, current_long REAL, distance_to_go REAL, 
                  last_updated TEXT, PRIMARY KEY(session_name, username))''')
    conn.commit()
    return conn

conn = init_db()

# --- GEOLOCATION LOGIC ---
def get_browser_location():
    """Attempts to get GPS via JS. Returns None if denied or unavailable."""
    js_code = """
    <script>
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            window.parent.postMessage({
                type: 'streamlit:set_component_value',
                value: {lat: pos.coords.latitude, lon: pos.coords.longitude}
            }, '*');
        },
        (err) => { console.error("Location denied"); },
        { enableHighAccuracy: true }
    );
    </script>
    """
    st.components.v1.html(js_code, height=0)
    return st.session_state.get("location_data")

# --- APP CONFIG ---
st.set_page_config(page_title="Geo-Quest Pro", layout="wide", page_icon="📍")
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: white; }
    .stButton>button { width: 100%; border-radius: 20px; }
    </style>
    """, unsafe_allow_html=True)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

# --- SIDEBAR: AUTH & USER INFO ---
with st.sidebar:
    st.title("🛡️ Player Portal")
    if not st.session_state.logged_in:
        mode = st.radio("Access", ["Login", "Sign Up"])
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.button("Enter Game"):
            if mode == "Login":
                res = conn.execute('SELECT * FROM users WHERE username=? AND password=?', (u, p)).fetchone()
                if res:
                    st.session_state.logged_in = True
                    st.session_state.username = u
                    st.rerun()
                else: st.error("Wrong credentials.")
            else:
                try:
                    conn.execute('INSERT INTO users VALUES (?,?,?)', (u, p, 0))
                    conn.commit()
                    st.success("Account Ready!")
                except: st.error("Name taken.")
    else:
        st.write(f"Logged in as: **{st.session_state.username}**")
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.rerun()

# --- MAIN CONTENT ---
if st.session_state.logged_in:
    tab1, tab2, tab3 = st.tabs(["🏠 Lobby", "🎯 Active Mission", "🏆 Hall of Fame"])

    with tab1:
        st.subheader("Create or Join a Server")
        c1, c2 = st.columns(2)
        
        with c1:
            st.write("### Host a Session")
            new_sess_name = st.text_input("Enter a unique Server Name", placeholder="e.g. Mimo-Elite-Race")
            if st.button("Launch Server"):
                if new_sess_name:
                    # Pick a random target within a 5km radius of a center point (example: Bankura area)
                    t_lat = 23.23 + random.uniform(-0.03, 0.03)
                    t_long = 87.07 + random.uniform(-0.03, 0.03)
                    try:
                        conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', 
                                     (new_sess_name, st.session_state.username, t_lat, t_long, "ACTIVE"))
                        conn.commit()
                        st.success(f"Server '{new_sess_name}' is LIVE!")
                    except: st.error("A server with that name already exists!")
                else: st.warning("Please name your server.")

        with c2:
            st.write("### Available Servers")
            active_df = pd.read_sql_query("SELECT session_name, creator FROM sessions WHERE status='ACTIVE'", conn)
            if not active_df.empty:
                st.dataframe(active_df, use_container_width=True, hide_index=True)
                join_name = st.selectbox("Select Server", active_df['session_name'].tolist())
                if st.button("Join"):
                    st.session_state.current_session = join_name
                    st.info(f"Connected to {join_name}")
            else:
                st.info("No active servers. Create one!")

    with tab2:
        if "current_session" in st.session_state:
            sess_data = conn.execute('SELECT * FROM sessions WHERE session_name=?', (st.session_state.current_session,)).fetchone()
            target = (sess_data[2], sess_data[3])
            
            st.write(f"## Mission: {st.session_state.current_session}")
            
            # --- LOCATION HANDLING ---
            loc = get_browser_location()
            
            if not loc:
                st.warning("⚠️ Automatic GPS unavailable. Please enter your coordinates manually below.")
                col_lat, col_lon = st.columns(2)
                manual_lat = col_lat.number_input("Your Latitude", value=23.2300, format="%.6f")
                manual_lon = col_lon.number_input("Your Longitude", value=87.0700, format="%.6f")
                current_coords = (manual_lat, manual_lon)
            else:
                current_coords = (loc['lat'], loc['lon'])
                st.success("✅ Real-time GPS Locked")

            # Calculate distance
            dist = geodesic(current_coords, target).meters
            
            # Update Live Progress in DB
            conn.execute('INSERT OR REPLACE INTO progress VALUES (?,?,?,?,?,?)',
                         (st.session_state.current_session, st.session_state.username, 
                          current_coords[0], current_coords[1], dist, datetime.now().isoformat()))
            conn.commit()

            # --- VISUALS ---
            m_col, d_col = st.columns([3, 1])
            with m_col:
                m = folium.Map(location=current_coords, zoom_start=14)
                folium.Marker(current_coords, popup="You", icon=folium.Icon(color='blue')).add_to(m)
                folium.Marker(target, popup="Goal", icon=folium.Icon(color='red', icon='star')).add_to(m)
                st_folium(m, width="100%", height=500)

            with d_col:
                st.metric("Distance", f"{dist:.1f} m")
                if dist < 30:
                    st.balloons()
                    st.success("TARGET REACHED!")
                    if st.button("Claim Victory"):
                        conn.execute('UPDATE users SET total_score = total_score + 100 WHERE username=?', (st.session_state.username,))
                        conn.commit()
                        st.rerun()

            # --- MULTIPLAYER LIST ---
            st.write("### 🏃 Live Opponents")
            opps = pd.read_sql_query("SELECT username, distance_to_go FROM progress WHERE session_name=? ORDER BY distance_to_go ASC", 
                                    conn, params=(st.session_state.current_session,))
            st.table(opps)
        else:
            st.write("Join a server in the Lobby to start the race!")

    with tab3:
        st.subheader("Global Rankings")
        ranks = pd.read_sql_query("SELECT username, total_score FROM users ORDER BY total_score DESC", conn)
        st.dataframe(ranks, use_container_width=True, hide_index=True)

else:
    st.header("Welcome to Geo-Quest AI")
    st.write("A real-time location-based multiplayer race. Sign in to start.")