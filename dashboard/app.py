import pandas as pd
import plotly.express as px
import streamlit as st
from google.cloud import bigquery

import transforms as T

PROJECT = "msbai-dwd-csc9720"
TABLE = f"{PROJECT}.citibike.daily_summary_with_weather"

st.set_page_config(page_title="NYC Citibike & Weather", layout="wide")


@st.cache_data(ttl=3600)
def load_data():
    """Read the small (~14k-row) daily table ONCE per hour and cache it.
    Every slider move reruns this script top-to-bottom; the cache is what
    keeps that rerun off BigQuery so the dashboard stays sub-second."""
    client = bigquery.Client(project=PROJECT)
    df = client.query(f"SELECT * FROM `{TABLE}`").to_dataframe()
    df["trip_date"] = pd.to_datetime(df["trip_date"])
    return df


df = load_data()

st.title("NYC Citibike Ridership & Weather")
st.caption(
    "Explore how day-to-day weather relates to Citibike ridership across "
    "NYC and Jersey City, 2013-2026. Data: BigQuery `citibike.daily_summary_with_weather` "
    "(trips) joined with `nyu-datasets.weather.m_weather_daily_nyc` (Central Park station)."
)

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
min_date, max_date = df["trip_date"].min().date(), df["trip_date"].max().date()
st.sidebar.header("Filters")
date_range = st.sidebar.date_input(
    "Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = min_date, max_date

systems = sorted(df["system"].dropna().unique())
selected_systems = st.sidebar.multiselect("Region", systems, default=systems)

rider_types = sorted(df["member_casual"].dropna().unique())
selected_riders = st.sidebar.multiselect("Rider type", rider_types, default=rider_types)

mask = (
    (df["trip_date"].dt.date >= start_date)
    & (df["trip_date"].dt.date <= end_date)
    & (df["system"].isin(selected_systems))
    & (df["member_casual"].isin(selected_riders))
)
fdf = df[mask].copy()

if fdf.empty:
    st.warning("No data for the selected filters. Adjust the filters in the sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
k = T.kpis(fdf)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total trips", f"{k['total_trips']:,}")
c2.metric("Days covered", f"{k['num_days']:,}")
c3.metric("Avg trips / day", f"{k['avg_daily_trips']:,.0f}")
c4.metric("Avg trip duration", f"{k['avg_duration_min']:,.1f} min")
c5.metric("Avg straight-line dist.", f"{k['avg_distance_km']:,.2f} km")

st.divider()

# ---------------------------------------------------------------------------
# Chart 1: Daily trips over time with rolling average
# ---------------------------------------------------------------------------
st.subheader("1. Daily ridership over time")
daily = T.daily_trips(fdf)

fig1 = px.line(
    daily,
    x="trip_date",
    y=["num_trips", "rolling_28d"],
    labels={"value": "Trips", "trip_date": "Date", "variable": ""},
)
fig1.data[0].name = "Daily trips"
fig1.data[1].name = "28-day rolling average"
st.plotly_chart(fig1, use_container_width=True)

peak_day = daily.loc[daily["num_trips"].idxmax()]
trough = daily.loc[daily["num_trips"].idxmin()]
st.caption(
    f"Ridership swings hugely with the seasons: the busiest day in the "
    f"selection had {int(peak_day['num_trips']):,} trips ({peak_day['trip_date'].date()}), "
    f"vs. just {int(trough['num_trips']):,} on the quietest day "
    f"({trough['trip_date'].date()}) - roughly a "
    f"{peak_day['num_trips'] / max(trough['num_trips'], 1):,.0f}x difference between "
    f"a peak summer day and a winter low."
)

st.divider()

# ---------------------------------------------------------------------------
# Chart 2: Daily trips vs avg temperature, by rider type
# ---------------------------------------------------------------------------
st.subheader("2. Ridership vs. temperature")
temp_df = fdf.dropna(subset=["tavg_f"]).copy()
fig2 = px.scatter(
    temp_df,
    x="tavg_f",
    y="num_trips",
    color="member_casual",
    opacity=0.4,
    labels={"tavg_f": "Average daily temperature (F)", "num_trips": "Trips", "member_casual": "Rider type"},
)
st.plotly_chart(fig2, use_container_width=True)

corr = T.temp_correlation(fdf)
corr_text = ", ".join(f"{k}: r={v:.2f}" for k, v in corr.items())
st.caption(
    f"Warmer days mean more trips for both rider types, but casual riders are far more "
    f"weather-sensitive than members (correlation of daily temperature with trip "
    f"counts - {corr_text}). On the coldest days ridership for casual riders nearly "
    f"flatlines, while members keep commuting year-round."
)

st.divider()

# ---------------------------------------------------------------------------
# Chart 3: Temperature-bucket breakdown of member vs casual share
# ---------------------------------------------------------------------------
st.subheader("3. Casual-rider share by temperature band")
share = T.casual_share_by_band(fdf)

fig3 = px.bar(
    share,
    x="temp_band",
    y="pct",
    color="member_casual",
    barmode="stack",
    category_orders={"temp_band": T.TEMP_LABELS},
    labels={"temp_band": "Average daily temperature", "pct": "Share of trips (%)", "member_casual": "Rider type"},
)
st.plotly_chart(fig3, use_container_width=True)

if "casual" in share["member_casual"].unique():
    casual_share = share[share["member_casual"] == "casual"].set_index("temp_band")["pct"]
    if "<32F (freezing)" in casual_share.index and "80F+" in casual_share.index:
        st.caption(
            f"Casual riders make up only {casual_share.get('<32F (freezing)', 0):.1f}% of "
            f"trips on freezing days (<32F), but {casual_share.get('80F+', 0):.1f}% of "
            f"trips on hot days (80F+) - nearly a "
            f"{casual_share.get('80F+', 0) / max(casual_share.get('<32F (freezing)', 0), 0.1):.0f}x "
            f"jump. Warm-weather promotions aimed at casual/tourist riders would land "
            f"on exactly the days those riders are already most active."
        )

st.divider()

# ---------------------------------------------------------------------------
# Chart 4: Rain/snow impact comparison
# ---------------------------------------------------------------------------
st.subheader("4. Impact of rain and snow")
avg_by_type = T.avg_by_daytype(fdf)

fig4 = px.bar(
    avg_by_type,
    x="day_type",
    y="avg_trips",
    color="member_casual",
    barmode="group",
    category_orders={"day_type": ["Dry", "Rainy", "Snowy"]},
    labels={"day_type": "Day type", "avg_trips": "Avg trips / day", "member_casual": "Rider type"},
)
st.plotly_chart(fig4, use_container_width=True)

try:
    pivot = avg_by_type.pivot(index="member_casual", columns="day_type", values="avg_trips")
    if "Dry" in pivot.columns and "Snowy" in pivot.columns:
        drops = []
        for rider in pivot.index:
            dry, snowy = pivot.loc[rider, "Dry"], pivot.loc[rider, "Snowy"]
            if pd.notna(dry) and pd.notna(snowy) and dry > 0:
                pct_drop = (1 - snowy / dry) * 100
                drops.append(f"{rider} ridership drops {pct_drop:.0f}% on snowy days")
        if drops:
            st.caption(
                "Compared to a dry day, " + "; ".join(drops) + " (vs. dry-day averages)."
            )
except (KeyError, ValueError):
    pass

st.divider()

# ---------------------------------------------------------------------------
# Chart 5: "Is it us or the weather?" - actual vs expected (same month-of-year baseline)
# ---------------------------------------------------------------------------
st.subheader("5. Is it us or the weather? Actual vs. expected ridership")
st.caption(
    "Expected ridership = the average number of trips on that calendar month "
    "across all years in the selection (a simple seasonal baseline). Large gaps "
    "between actual and expected suggest something other than typical seasonal "
    "weather is driving the change - a service disruption, a major weather event, "
    "or a shift in how many bikes/stations are available."
)

monthly = T.actual_vs_expected(fdf)

fig5 = px.line(
    monthly,
    x="trip_date",
    y=["num_trips", "expected"],
    labels={"value": "Trips / month", "trip_date": "Month", "variable": ""},
)
fig5.data[0].name = "Actual"
fig5.data[1].name = "Expected (seasonal baseline)"
st.plotly_chart(fig5, use_container_width=True)

worst = monthly.loc[monthly["pct_diff"].idxmin()]
best = monthly.loc[monthly["pct_diff"].idxmax()]
st.caption(
    f"The biggest shortfall vs. the seasonal baseline was "
    f"{worst['trip_date'].strftime('%B %Y')}, at {worst['pct_diff']:.0f}% below expected "
    f"({int(worst['num_trips']):,} vs. {int(worst['expected']):,} expected trips) - "
    f"likely a real disruption rather than ordinary weather. The biggest surplus was "
    f"{best['trip_date'].strftime('%B %Y')}, at +{best['pct_diff']:.0f}% above expected "
    f"({int(best['num_trips']):,} vs. {int(best['expected']):,} expected)."
)

st.divider()
st.caption(
    "Source: Citibike trip data (2013-2026) from public S3 archives, processed via "
    "GCS -> BigQuery ETL pipeline. Weather: `nyu-datasets.weather.m_weather_daily_nyc`. "
    "`distance_km_straight_line` is the great-circle distance between start/end "
    "stations, not actual ride distance."
)
