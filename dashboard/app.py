import math
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from google.cloud import bigquery

import transforms as T
from transforms import (
    build_weather_model,
    compute_residuals,
    fetch_forecast,
    predict_forecast,
    ebike_revenue,
)

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

# ── Stretch 1: Weather-adjusted ridership model ──────────────────────────────
st.divider()
st.subheader("6. Weather-Adjusted Ridership Model")
st.info("Weather model uses full history — not affected by sidebar filters.")


@st.cache_data(ttl=3600)
def get_model_results(_df):
    return build_weather_model(_df)


with st.spinner("Fitting weather model (runs once, then cached)…"):
    model, mape_pct, test_df = get_model_results(df)

residuals_df = compute_residuals(df, model)
std_resid = residuals_df["residual_pct"].std()
residuals_df["outlier"] = residuals_df["residual_pct"].abs() > 2 * std_resid

fig6a = go.Figure()
fig6a.add_trace(go.Scatter(x=residuals_df["trip_date"], y=residuals_df["num_trips"],
    mode="lines", name="Actual", line=dict(color="#636EFA")))
fig6a.add_trace(go.Scatter(x=residuals_df["trip_date"], y=residuals_df["predicted_trips"],
    mode="lines", name="Model expected", line=dict(color="#EF553B", dash="dot")))
fig6a.update_layout(title="Actual vs. Weather-Expected Daily Trips (all regions, all rider types)",
    xaxis_title="Date", yaxis_title="Trips", legend=dict(orientation="h"))
st.plotly_chart(fig6a, use_container_width=True)

colors = ["red" if o else "#AB63FA" for o in residuals_df["outlier"]]
fig6b = go.Figure()
fig6b.add_trace(go.Bar(x=residuals_df["trip_date"], y=residuals_df["residual_pct"],
    marker_color=colors, name="Residual %"))
fig6b.add_hline(y=0, line_dash="dash", line_color="gray")
fig6b.update_layout(title="Residual % (Actual − Expected) / Expected — outliers flagged in red",
    xaxis_title="Date", yaxis_title="Residual %")
st.plotly_chart(fig6b, use_container_width=True)
st.caption(
    "Days when actual falls far below the weather-based expectation signal an operational problem, "
    "not a weather event. "
    f"Model: GradientBoostingRegressor trained on pre-2024 data; test-set MAPE = {mape_pct:.1f}%. "
    "Note: model uses full unfiltered dataset (aggregate of all systems/rider types)."
)

# ── Stretch 2: 7-day forecast ─────────────────────────────────────────────────
st.divider()
st.subheader("7. 7-Day Ridership Forecast")
st.info("Weather model uses full history — not affected by sidebar filters.")


@st.cache_data(ttl=3600)
def get_forecast():
    return fetch_forecast()


forecast_raw = get_forecast()
if forecast_raw is None:
    st.warning("Forecast unavailable — could not reach Open-Meteo API. Try again later.")
else:
    forecast_pred = predict_forecast(model, forecast_raw)
    fig7 = go.Figure()
    fig7.add_trace(go.Bar(
        x=forecast_pred["trip_date"].dt.strftime("%a %b %d"),
        y=forecast_pred["predicted_trips"].round().astype(int),
        marker_color="#00CC96",
        name="Predicted trips",
    ))
    fig7.update_layout(title="Predicted Daily Trips — Next 7 Days (NYC coordinates)",
        xaxis_title="Date", yaxis_title="Predicted trips")
    st.plotly_chart(fig7, use_container_width=True)
    st.caption(
        f"This model predicted 2024+ ridership within {mape_pct:.1f}% on held-out data it had never seen. "
        "Forecast weather from Open-Meteo (open-meteo.com); model trained on Citibike history 2013–2023. "
        "All regions and rider types combined."
    )

# ── Stretch 3: E-bike revenue estimate ───────────────────────────────────────
st.divider()
st.subheader("8. E-Bike Revenue Estimate")

ebike_df = ebike_revenue(fdf)

if ebike_df.empty:
    st.info("No e-bike data available for the current filter selection (requires 2021-02+ and electric trips > 0).")
else:
    total_rev = ebike_df["est_revenue"].sum()
    st.metric("Total estimated e-bike revenue (selection)", f"${total_rev:,.0f}")

    # Grouped bar: avg daily revenue on Dry/Rainy/Snowy days by member_casual
    ebike_weather = fdf[
        (pd.to_datetime(fdf["trip_date"]) >= pd.Timestamp("2021-02-01"))
        & (fdf["num_electric_trips"] > 0)
    ].copy()
    if "prcp_in" in ebike_weather.columns and "snow_in" in ebike_weather.columns:
        def weather_cat(row):
            if row["snow_in"] > 0.1:
                return "Snowy"
            elif row["prcp_in"] > 0.1:
                return "Rainy"
            else:
                return "Dry"
        ebike_weather["weather_cat"] = ebike_weather.apply(weather_cat, axis=1)
        # merge revenue
        rev_by_day = ebike_df.groupby(["trip_date", "member_casual"])["est_revenue"].sum().reset_index()
        ebike_weather_daily = ebike_weather.groupby(["trip_date", "member_casual"]).agg(
            prcp_in=("prcp_in", "first"), snow_in=("snow_in", "first")
        ).reset_index()
        ebike_weather_daily["weather_cat"] = ebike_weather_daily.apply(weather_cat, axis=1)
        merged_w = ebike_weather_daily.merge(rev_by_day, on=["trip_date", "member_casual"], how="left")
        avg_by_weather = merged_w.groupby(["weather_cat", "member_casual"])["est_revenue"].mean().reset_index()

        fig8a = px.bar(avg_by_weather, x="weather_cat", y="est_revenue", color="member_casual",
            barmode="group", category_orders={"weather_cat": ["Dry", "Rainy", "Snowy"]},
            labels={"est_revenue": "Avg daily e-bike revenue ($)", "weather_cat": "Weather", "member_casual": "Rider type"},
            title="Avg Daily E-Bike Revenue by Weather Condition and Rider Type")
        st.plotly_chart(fig8a, use_container_width=True)

        # Headline
        dry_rev = avg_by_weather[avg_by_weather["weather_cat"] == "Dry"]["est_revenue"].sum()
        snowy_rev = avg_by_weather[avg_by_weather["weather_cat"] == "Snowy"]["est_revenue"].sum()
        if snowy_rev > 0 and dry_rev > 0:
            st.markdown(f"**A snowy day costs ~${dry_rev - snowy_rev:,.0f} vs a dry day in e-bike revenue.**")

    # Line chart: monthly total e-bike revenue 2021+
    ebike_df2 = ebike_df.copy()
    ebike_df2["month"] = ebike_df2["trip_date"].dt.to_period("M").dt.to_timestamp()
    monthly_rev = ebike_df2.groupby("month")["est_revenue"].sum().reset_index()
    fig8b = px.line(monthly_rev, x="month", y="est_revenue",
        labels={"est_revenue": "Monthly e-bike revenue ($)", "month": "Month"},
        title="Monthly Total Estimated E-Bike Revenue (Feb 2021+)")
    st.plotly_chart(fig8b, use_container_width=True)

    st.caption(
        "Pricing schedule (Citibike 2025 rates applied as a historical yardstick): "
        "Casual e-bike: $1.00 unlock + $0.26/min; Member e-bike: $0.17/min. "
        "Scope: Feb 2021+ rows where num_electric_trips > 0. "
        "avg_trip_duration_minutes is the group-level average (not e-bike specific) — "
        "revenue figures are estimates only. Today's prices applied historically."
    )

st.divider()
st.caption(
    "Source: Citibike trip data (2013-2026) from public S3 archives, processed via "
    "GCS -> BigQuery ETL pipeline. Weather: `nyu-datasets.weather.m_weather_daily_nyc`. "
    "`distance_km_straight_line` is the great-circle distance between start/end "
    "stations, not actual ride distance."
)
