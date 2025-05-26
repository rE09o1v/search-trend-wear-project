import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from pathlib import Path
import datetime
import time
import re

try:
    from scraper import (
        scrape_prices_for_keyword_and_site,
        save_daily_stats_for_site,
        DATA_DIR,
        SITE_CONFIGS,
    )
except ImportError as e:
    st.error(f"scraper.pyのインポートに失敗しました: {e}")
    st.stop()