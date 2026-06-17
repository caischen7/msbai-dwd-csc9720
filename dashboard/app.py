"""
NYC Citibike + Weather Dashboard
=================================
Reads citibike.daily_summary_with_weather (~14k rows) ONCE per hour,
caches it, and serves all charts from local pandas — no BigQuery per click.

Tabs
----
1. Overview          — KPIs, ridership trend, YoY growth, weekday/weekend split
2. Weather Impact    — Temperature scatter, casual-share bands, rain/snow bars
3. Stretch: Model    — Weather-adjusted model, actual vs expected, anomaly table
4. Stretch: Forecast — 7-day predicted ridership from Open-Meteo forecast API
5. Stretch: Revenue  — E-bike revenue estimate (Citibike 2025 pricing, 2021+)
"""

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

# ── config ────────────────────────────────────────────────────────────────────
PROJECT = "msbai-dwd-csc9720"
TABLE = f"{PROJECT}.citibike.daily_summary_with_weather"

st.set_page_config(
    page_title="NYC Citibike & Weather",
    page_icon="🚲",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── color palette (consistent across all charts) ──────────────────────────────
COLORS = {"member": "#1F77B4", "casual": "#FF7F0E"}


# ── data load (cached 1 hour) ─────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_data():
    client = bigquery.Client(project=PROJECT)
    df = client.query(f"SELECT * FROM `{TABLE}`").to_dataframe()
    df["trip_date"] = pd.to_datetime(df["trip_date"])
    return df


df = load_data()

# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚲 Citibike Dashboard")
    st.markdown("---")
    st.markdown("### Filters")

    min_date = df["trip_date"].min().date()
    max_date = df["trip_date"].max().date()
    date_range = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = min_date, max_date

    systems = sorted(df["system"].dropna().unique())
    selected_systems = st.multiselect("Region", systems, default=systems)

    rider_types = sorted(df["member_casual"].dropna().unique())
    selected_riders = st.multiselect("Rider type", rider_types, default=rider_types)

    st.markdown("---")
    st.markdown("### About")
    st.markdown(
        "**Data:** 316M Citibike trips (2013–2026) loaded from public S3 archives "
        "into BigQuery via a custom ETL pipeline.\n\n"
        "**Weather:** Central Park daily station "
        "(`nyu-datasets.weather.m_weather_daily_nyc`).\n\n"
        "**Speed:** The ~14k-row daily table is read once and cached. "
        "Sliders run on pandas — no BigQuery per click.\n\n"
        "**Project:** `msbai-dwd-csc9720`"
    )
    st.markdown("---")
    st.markdown(
        "**Stretch features**\n"
        "- 🔬 Weather model (Tab 3)\n"
        "- 🔮 7-day forecast (Tab 4)\n"
        "- 💰 E-bike revenue (Tab 5)"
    )

# ── apply filters ─────────────────────────────────────────────────────────────
mask = (
    (df["trip_date"].dt.date >= start_date)
    & (df["trip_date"].dt.date <= end_date)
    & (df["system"].isin(selected_systems))
    & (df["member_casual"].isin(selected_riders))
)
fdf = df[mask].copy()

if fdf.empty:
    st.warning("No data for the selected filters. Adjust the sidebar.")
    st.stop()

# ── page header ───────────────────────────────────────────────────────────────
st.title("🚲 NYC Citibike Ridership & Weather")
st.caption(
    "How does New York City weather shape Citibike ridership? "
    "Explore 13 years of trip data — filtered by date, region, and rider type. "
    "Tabs 3–5 are the optional **stretch deliverables**: a weather-adjusted model, "
    "a 7-day forecast, and an e-bike revenue estimate."
)

# ── KPI row ───────────────────────────────────────────────────────────────────
k = T.kpis(fdf)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total trips", f"{k['total_trips']:,}")
c2.metric("Days covered", f"{k['num_days']:,}")
c3.metric("Avg trips / day", f"{k['avg_daily_trips']:,.0f}")
c4.metric("Avg trip duration", f"{k['avg_duration_min']:,.1f} min")
c5.metric("Avg straight-line dist.", f"{k['avg_distance_km']:,.2f} km")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Overview",
    "🌡️ Weather Impact",
    "🔬 Model (Stretch 1)",
    "🔮 Forecast (Stretch 2)",
    "💰 E-Bike Revenue (Stretch 3)",
])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1 — OVERVIEW
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab1:

    # Chart 1: Daily trips + rolling average
    st.subheader("Daily ridership over time")
    daily = T.daily_trips(fdf)
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=daily["trip_date"], y=daily["num_trips"],
        mode="lines", name="Daily trips",
        line=dict(color="#A8C8FF", width=1), opacity=0.6,
    ))
    fig1.add_trace(go.Scatter(
        x=daily["trip_date"], y=daily["rolling_28d"],
        mode="lines", name="28-day rolling avg",
        line=dict(color="#1F77B4", width=2.5),
    ))
    fig1.update_layout(
        xaxis_title="Date", yaxis_title="Trips",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        hovermode="x unified",
    )
    st.plotly_chart(fig1, use_container_width=True)
    peak = daily.loc[daily["num_trips"].idxmax()]
    trough = daily.loc[daily["num_trips"].idxmin()]
    ratio = peak["num_trips"] / max(trough["num_trips"], 1)
    st.caption(
        f"The busiest day had **{int(peak['num_trips']):,} trips** ({peak['trip_date'].date()}); "
        f"the quietest had {int(trough['num_trips']):,} ({trough['trip_date'].date()}) — "
        f"a **{ratio:,.0f}× seasonal swing**."
    )

    st.divider()

    # Chart 2: Actual vs seasonal baseline
    st.subheader("Actual vs. seasonal baseline — 'Is it us or the weather?'")
    st.caption(
        "Expected = average trips for that calendar month across all years in the selection. "
        "Months far below baseline signal disruptions beyond ordinary seasonality."
    )
    monthly = T.actual_vs_expected(fdf)
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=monthly["trip_date"], y=monthly["num_trips"],
        mode="lines+markers", name="Actual",
        line=dict(color="#1F77B4", width=2), marker=dict(size=4),
    ))
    fig2.add_trace(go.Scatter(
        x=monthly["trip_date"], y=monthly["expected"],
        mode="lines", name="Expected (seasonal baseline)",
        line=dict(color="#FF7F0E", width=2, dash="dot"),
    ))
    fig2.update_layout(
        xaxis_title="Month", yaxis_title="Trips / month",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        hovermode="x unified",
    )
    st.plotly_chart(fig2, use_container_width=True)
    worst = monthly.loc[monthly["pct_diff"].idxmin()]
    best  = monthly.loc[monthly["pct_diff"].idxmax()]
    st.caption(
        f"Biggest shortfall: **{worst['trip_date'].strftime('%B %Y')}** "
        f"({worst['pct_diff']:.0f}% below expected). "
        f"Biggest surplus: **{best['trip_date'].strftime('%B %Y')}** "
        f"(+{best['pct_diff']:.0f}% above expected)."
    )

    st.divider()

    # Chart 3: Year-over-year growth
    st.subheader("Year-over-year growth by region")
    yoy = T.yoy_growth(fdf)
    if not yoy.empty:
        fig3 = px.bar(
            yoy, x="year", y="num_trips", color="system", barmode="group",
            color_discrete_map={"NYC": "#1F77B4", "JC": "#FF7F0E"},
            labels={"year": "Year", "num_trips": "Total trips", "system": "Region"},
            text=yoy["num_trips"].apply(lambda x: f"{x/1e6:.1f}M"),
        )
        fig3.update_traces(textposition="outside")
        st.plotly_chart(fig3, use_container_width=True)
        nyc = yoy[yoy["system"] == "NYC"].set_index("year")["num_trips"]
        if len(nyc) >= 2:
            first_yr, last_yr = nyc.index.min(), nyc.index.max()
            cagr = ((nyc[last_yr] / nyc[first_yr]) ** (1 / max(last_yr - first_yr, 1)) - 1) * 100
            st.caption(
                f"NYC grew from {nyc[first_yr]/1e6:.1f}M trips in {first_yr} to "
                f"{nyc[last_yr]/1e6:.1f}M in {last_yr} — a **{cagr:.1f}% CAGR**. "
                "Years below the trend (e.g. 2020) reflect disruptions weather alone can't explain."
            )

    st.divider()

    # Chart 4: Weekday vs weekend
    st.subheader("Weekday vs. weekend — commuters vs. leisure riders")
    ww = T.weekday_vs_weekend(fdf)
    fig4 = px.bar(
        ww, x="day_class", y="avg_trips", color="member_casual", barmode="group",
        color_discrete_map=COLORS,
        category_orders={"day_class": ["Weekday", "Weekend"]},
        labels={"day_class": "", "avg_trips": "Avg trips / day", "member_casual": "Rider type"},
        text=ww["avg_trips"].apply(lambda x: f"{int(x):,}"),
    )
    fig4.update_traces(textposition="outside")
    st.plotly_chart(fig4, use_container_width=True)
    try:
        pivot = ww.pivot(index="member_casual", columns="day_class", values="avg_trips")
        casual_lift  = (pivot.loc["casual",  "Weekend"] / pivot.loc["casual",  "Weekday"] - 1) * 100
        member_drop  = (1 - pivot.loc["member", "Weekend"] / pivot.loc["member", "Weekday"]) * 100
        st.caption(
            f"Members ride **{member_drop:.0f}% less** on weekends (commuters don't need the bike). "
            f"Casual riders ride **{casual_lift:.0f}% more** on weekends — leisure and tourism peak "
            "when offices are closed. Weekend promotions hit the high-margin casual segment at peak demand."
        )
    except (KeyError, ValueError):
        pass

    st.divider()

    # Chart 5: Day-of-week × month heatmap
    st.subheader("When do people ride? Day-of-week × month heatmap")
    heatmap = T.dow_month_heatmap(fdf)
    fig5 = px.imshow(
        heatmap, color_continuous_scale="Blues", aspect="auto", text_auto=".0f",
        labels={"x": "Month", "y": "Day of week", "color": "Avg trips/day"},
    )
    fig5.update_xaxes(side="top")
    st.plotly_chart(fig5, use_container_width=True)
    peak_dow   = heatmap.mean(axis=1).idxmax()
    peak_month = heatmap.mean(axis=0).idxmax()
    st.caption(
        f"Ridership peaks on **{peak_dow}s in {peak_month}** — the busiest day-of-week meets "
        "the busiest season. Weekday patterns stay steady year-round; summer weekends spike."
    )

    st.divider()

    # Chart 6: Fleet shift (classic → electric)
    st.subheader("Fleet shift: classic → electric bikes (2021 onward)")
    fleet = T.fleet_shift(fdf)
    if fleet.empty:
        st.info("Extend the date range to 2021+ to see e-bike adoption data.")
    else:
        fig6 = px.line(
            fleet, x="trip_date", y="pct_electric", color="system",
            color_discrete_map={"NYC": "#1F77B4", "JC": "#FF7F0E"},
            labels={"trip_date": "Month", "pct_electric": "E-bike share (%)", "system": "Region"},
        )
        fig6.add_hline(y=50, line_dash="dash", line_color="gray",
                       annotation_text="50%", annotation_position="right")
        fig6.update_layout(hovermode="x unified")
        st.plotly_chart(fig6, use_container_width=True)
        latest = fleet.groupby("system")["pct_electric"].last()
        parts  = [f"{s}: {p:.0f}%" for s, p in latest.items()]
        st.caption(
            f"E-bikes have grown to {', '.join(parts)} of trips since Feb 2021. "
            "The shift matters for revenue: e-bike trips carry a per-minute surcharge that classic trips (for members) do not."
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2 — WEATHER IMPACT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab2:

    # Chart 7: Temperature scatter + trendlines
    st.subheader("Ridership vs. temperature")
    temp_df = fdf.dropna(subset=["tavg_f"]).copy()
    fig7 = px.scatter(
        temp_df, x="tavg_f", y="num_trips", color="member_casual",
        color_discrete_map=COLORS, opacity=0.3,
        trendline="lowess", trendline_options={"frac": 0.3},
        labels={"tavg_f": "Avg daily temperature (°F)", "num_trips": "Trips",
                "member_casual": "Rider type"},
    )
    fig7.update_traces(marker=dict(size=4))
    fig7.update_layout(legend=dict(orientation="h", y=1.02, x=1, xanchor="right"))
    st.plotly_chart(fig7, use_container_width=True)
    corr = T.temp_correlation(fdf)
    corr_str = ", ".join(f"{k}: r={v:.2f}" for k, v in corr.items())
    st.caption(
        f"Warmer days mean more trips for everyone, but **casual riders are far more "
        f"weather-sensitive** ({corr_str}). Members commute year-round; casual riders "
        "nearly disappear on cold days. Trendlines are LOWESS smoothed fits."
    )

    st.divider()

    # Chart 8: Casual share by temperature band
    st.subheader("Casual-rider share by temperature band")
    share = T.casual_share_by_band(fdf)
    fig8 = px.bar(
        share, x="temp_band", y="pct", color="member_casual", barmode="stack",
        color_discrete_map=COLORS,
        category_orders={"temp_band": T.TEMP_LABELS},
        labels={"temp_band": "Avg daily temperature", "pct": "Share of trips (%)",
                "member_casual": "Rider type"},
    )
    fig8.update_layout(legend=dict(orientation="h", y=1.02, x=1, xanchor="right"))
    st.plotly_chart(fig8, use_container_width=True)
    if "casual" in share["member_casual"].unique():
        cs = share[share["member_casual"] == "casual"].set_index("temp_band")["pct"]
        cold = cs.get("<32F (freezing)", 0)
        hot  = cs.get("80F+", 0)
        if cold > 0 and hot > 0:
            st.caption(
                f"Casual riders are only **{cold:.1f}%** of trips on freezing days, "
                f"but **{hot:.1f}%** on 80°F+ days — a **{hot/max(cold,0.1):.0f}× jump**. "
                "Weather-triggered promotions land on exactly the days casual riders are most active."
            )

    st.divider()

    # Chart 9: Rain and snow impact
    st.subheader("Impact of rain and snow")
    avg_by_type = T.avg_by_daytype(fdf)
    fig9 = px.bar(
        avg_by_type, x="day_type", y="avg_trips", color="member_casual", barmode="group",
        color_discrete_map=COLORS,
        category_orders={"day_type": ["Dry", "Rainy", "Snowy"]},
        text=avg_by_type["avg_trips"].apply(lambda x: f"{int(x):,}"),
        labels={"day_type": "Day type", "avg_trips": "Avg trips / day",
                "member_casual": "Rider type"},
    )
    fig9.update_traces(textposition="outside")
    fig9.update_layout(legend=dict(orientation="h", y=1.02, x=1, xanchor="right"))
    st.plotly_chart(fig9, use_container_width=True)
    try:
        p = avg_by_type.pivot(index="member_casual", columns="day_type", values="avg_trips")
        drops = []
        for rider in p.index:
            if "Dry" in p.columns and "Snowy" in p.columns:
                dry, snowy = p.loc[rider, "Dry"], p.loc[rider, "Snowy"]
                if pd.notna(dry) and pd.notna(snowy) and dry > 0:
                    drops.append(f"**{rider}** −{(1-snowy/dry)*100:.0f}%")
        if drops:
            st.caption(
                "On snowy days vs dry days: " + ", ".join(drops) + ". "
                "Bad weather hurts casual riders (the high-margin segment) most."
            )
    except (KeyError, ValueError):
        pass

    st.divider()

    # Chart 10: Temperature distribution
    st.subheader("Temperature distribution of riding days")
    daily_temp = (
        fdf.dropna(subset=["tavg_f"])
        .groupby("trip_date")
        .agg(num_trips=("num_trips", "sum"), tavg_f=("tavg_f", "first"))
        .reset_index()
    )
    median_t = daily_temp["tavg_f"].median()
    fig10 = go.Figure()
    fig10.add_trace(go.Histogram(
        x=daily_temp["tavg_f"], nbinsx=30, marker_color="#1F77B4", opacity=0.7, name="Days",
    ))
    fig10.add_vline(x=median_t, line_dash="dash", line_color="gray",
                    annotation_text=f"Median {median_t:.0f}°F", annotation_position="top right")
    fig10.update_layout(xaxis_title="Avg daily temperature (°F)", yaxis_title="Number of days")
    st.plotly_chart(fig10, use_container_width=True)
    pct_warm = (daily_temp["tavg_f"] >= 65).mean() * 100
    st.caption(
        f"**{pct_warm:.0f}% of days in the selection are 65°F+** — the range where casual "
        "ridership climbs sharply. This sets the ceiling for how often weather-driven promotions can fire."
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3 — STRETCH 1: WEATHER-ADJUSTED MODEL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab3:
    st.subheader("🔬 Stretch 1 — Weather-Adjusted Ridership Model")
    st.markdown(
        """
A gradient-boosting model learns what ridership *should* look like given the weather,
day of week, and season. Days where actual ridership falls far below the model's
expectation signal an **operational problem** — not just bad weather.

**Model:** `GradientBoostingRegressor` (200 trees, depth 4, lr=0.05)
**Features:** `tavg_f`, `prcp_in`, `snow_in`, day-of-week sin/cos, month sin/cos, year
**Train/test split:** pre-2024 data (~3,800 days) → test on 2024+ (~500 days, held-out)
**Scope:** full dataset — sidebar filters do not change the model.
"""
    )

    @st.cache_data(ttl=3600)
    def get_model(_df_hash):
        return build_weather_model(df)

    with st.spinner("Fitting weather model (cached after first run)…"):
        model, mape_pct, _ = get_model(len(df))

    residuals_df = compute_residuals(df, model)
    std_r = residuals_df["residual_pct"].std()
    residuals_df["outlier"] = residuals_df["residual_pct"].abs() > 2 * std_r
    n_outliers = int(residuals_df["outlier"].sum())

    m1, m2 = st.columns(2)
    m1.metric("Out-of-sample MAPE (2024+)", f"{mape_pct:.1f}%",
              help="Mean Absolute % Error on 2024+ data never seen during training.")
    m2.metric("Flagged anomaly days (|residual| > 2σ)", str(n_outliers),
              help="Days where actual ridership diverges more than weather can explain.")

    # Actual vs expected
    fig_av = go.Figure()
    fig_av.add_trace(go.Scatter(
        x=residuals_df["trip_date"], y=residuals_df["num_trips"],
        mode="lines", name="Actual", line=dict(color="#1F77B4", width=1.5),
    ))
    fig_av.add_trace(go.Scatter(
        x=residuals_df["trip_date"], y=residuals_df["predicted_trips"],
        mode="lines", name="Weather-expected", line=dict(color="#FF7F0E", width=1.5, dash="dot"),
    ))
    fig_av.update_layout(
        title="Actual vs. Weather-Expected Daily Trips",
        xaxis_title="Date", yaxis_title="Trips",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        hovermode="x unified",
    )
    st.plotly_chart(fig_av, use_container_width=True)

    # Residuals bar
    colors_r = ["#E74C3C" if o else "#BDC3C7" for o in residuals_df["outlier"]]
    fig_res = go.Figure()
    fig_res.add_trace(go.Bar(
        x=residuals_df["trip_date"], y=residuals_df["residual_pct"],
        marker_color=colors_r, name="Residual %",
    ))
    fig_res.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_res.add_hline(y=2*std_r,  line_dash="dot", line_color="#E74C3C",
                      annotation_text="+2σ", annotation_position="right")
    fig_res.add_hline(y=-2*std_r, line_dash="dot", line_color="#E74C3C",
                      annotation_text="−2σ", annotation_position="right")
    fig_res.update_layout(
        title="Residual % — red bars are anomalies worth investigating",
        xaxis_title="Date", yaxis_title="Residual %",
    )
    st.plotly_chart(fig_res, use_container_width=True)
    st.caption(
        f"Model trained on pre-2024 data; **out-of-sample MAPE = {mape_pct:.1f}%** on 2024+ held-out data. "
        "Red bars (|residual| > 2σ) flag days where something beyond weather drove the gap — "
        "outages, events, or promotions."
    )

    # Top anomaly days table
    st.markdown("#### Top 10 anomaly days")
    top = (
        residuals_df[residuals_df["outlier"]]
        .assign(abs_r=lambda d: d["residual_pct"].abs())
        .nlargest(10, "abs_r")[["trip_date", "num_trips", "predicted_trips", "residual_pct", "tavg_f"]]
        .rename(columns={
            "trip_date": "Date", "num_trips": "Actual",
            "predicted_trips": "Expected", "residual_pct": "Residual %",
            "tavg_f": "Temp (°F)",
        })
    )
    top["Date"]       = top["Date"].dt.strftime("%Y-%m-%d")
    top["Actual"]     = top["Actual"].apply(lambda x: f"{int(x):,}")
    top["Expected"]   = top["Expected"].apply(lambda x: f"{int(x):,}")
    top["Residual %"] = top["Residual %"].apply(lambda x: f"{x:+.1f}%")
    top["Temp (°F)"]  = top["Temp (°F)"].apply(lambda x: f"{x:.1f}°F" if pd.notna(x) else "—")
    st.dataframe(top, use_container_width=True, hide_index=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4 — STRETCH 2: 7-DAY FORECAST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab4:
    st.subheader("🔮 Stretch 2 — 7-Day Ridership Forecast")
    st.markdown(
        """
The same weather model (Stretch 1) is applied to this week's weather forecast
from **[Open-Meteo](https://open-meteo.com/)** (free API, no key required).

**NYC coordinates:** 40.7128°N, 74.0060°W
**Accuracy on held-out 2024+ data:** displayed below as out-of-sample MAPE.
**Note:** forecast error compounds model error — treat as directional, not exact.
"""
    )

    if "model" not in dir() or model is None:
        @st.cache_data(ttl=3600)
        def get_model2(_df_hash):
            return build_weather_model(df)
        model, mape_pct, _ = get_model2(len(df))

    @st.cache_data(ttl=3600)
    def get_forecast():
        return fetch_forecast()

    forecast_raw = get_forecast()

    if forecast_raw is None:
        st.warning("⚠️ Could not reach the Open-Meteo forecast API. Try again later.")
    else:
        forecast_pred = predict_forecast(model, forecast_raw)

        bar_colors = [
            "#9B59B6" if row["snow_in"] > 0.1
            else "#3498DB" if row["prcp_in"] > 0.1
            else "#2ECC71"
            for _, row in forecast_raw.iterrows()
        ]

        fig_fc = go.Figure()
        fig_fc.add_trace(go.Bar(
            x=forecast_pred["trip_date"].dt.strftime("%a %b %d"),
            y=forecast_pred["predicted_trips"].round().astype(int),
            marker_color=bar_colors,
            text=forecast_pred["predicted_trips"].round().astype(int).apply(lambda x: f"{x:,}"),
            textposition="outside",
        ))
        fig_fc.update_layout(
            title="Predicted Daily Trips — Next 7 Days",
            xaxis_title="Date", yaxis_title="Predicted trips", showlegend=False,
        )
        st.plotly_chart(fig_fc, use_container_width=True)

        st.markdown("**Bar color:** 🟢 Dry &nbsp;|&nbsp; 🔵 Rainy &nbsp;|&nbsp; 🟣 Snowy")

        # Detail table
        detail = pd.DataFrame({
            "Date": forecast_pred["trip_date"].dt.strftime("%A, %b %d"),
            "Forecast temp (°F)": forecast_raw["tavg_f"].round(0).apply(lambda x: f"{x:.0f}°F"),
            "Precip (in)":  forecast_raw["prcp_in"].round(2).apply(lambda x: f"{x:.2f}\""),
            "Snow (in)":    forecast_raw["snow_in"].round(2).apply(lambda x: f"{x:.2f}\""),
            "Predicted trips": forecast_pred["predicted_trips"].round().astype(int).apply(lambda x: f"{x:,}"),
        })
        st.dataframe(detail, use_container_width=True, hide_index=True)

        st.caption(
            f"Model accuracy on held-out 2024+ data: **{mape_pct:.1f}% MAPE**. "
            "Weather forecast from Open-Meteo (open-meteo.com). "
            "Predictions are aggregate (all systems and rider types combined)."
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 5 — STRETCH 3: E-BIKE REVENUE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab5:
    st.subheader("💰 Stretch 3 — E-Bike Revenue Estimate")
    st.markdown(
        """
**Scope:** February 2021 onward, rows where `num_electric_trips > 0`
(bike type not recorded before Schema B / Feb 2021).

**Pricing schedule (Citibike 2025 rates — applied as a historical yardstick):**

| Rider type | Rate |
|-----------|------|
| Casual | $1.00 unlock fee + $0.26/min |
| Member | $0.17/min (no unlock fee; membership covers access) |

Classic bikes excluded — a member's classic trip has near-zero marginal revenue.
*Duration is the group-level average, not e-bike-specific — treat figures as ±20–30% estimates.*
**2025 pricing applied historically is a yardstick, not reconstructed revenue.**
"""
    )

    ebike_df = ebike_revenue(fdf)

    if ebike_df.empty:
        st.info("No e-bike data in the current selection. Extend the date range to include 2021+.")
    else:
        total_rev   = ebike_df["est_revenue"].sum()
        total_trips = int(ebike_df["num_electric_trips"].sum())
        avg_rev     = total_rev / max(total_trips, 1)

        r1, r2, r3 = st.columns(3)
        r1.metric("Total est. e-bike revenue", f"${total_rev:,.0f}")
        r2.metric("Total e-bike trips",        f"{total_trips:,}")
        r3.metric("Avg revenue / e-bike trip", f"${avg_rev:.2f}")

        st.divider()

        # Weather breakdown
        scope = fdf[
            (pd.to_datetime(fdf["trip_date"]) >= pd.Timestamp("2021-02-01"))
            & (fdf["num_electric_trips"] > 0)
        ].copy()

        if "prcp_in" in scope.columns and "snow_in" in scope.columns:
            def wcat(row):
                if row["snow_in"] > 0.1:  return "Snowy"
                if row["prcp_in"] > 0.1:  return "Rainy"
                return "Dry"

            scope["wcat"] = scope.apply(wcat, axis=1)
            daily_wcat = scope.groupby(["trip_date", "member_casual"]).agg(
                prcp_in=("prcp_in", "first"), snow_in=("snow_in", "first")
            ).reset_index()
            daily_wcat["wcat"] = daily_wcat.apply(wcat, axis=1)

            rev_daily = ebike_df.groupby(["trip_date", "member_casual"])["est_revenue"].sum().reset_index()
            merged = daily_wcat.merge(rev_daily, on=["trip_date", "member_casual"], how="left")
            avg_wrev = merged.groupby(["wcat", "member_casual"])["est_revenue"].mean().reset_index()

            fig_wr = px.bar(
                avg_wrev, x="wcat", y="est_revenue", color="member_casual", barmode="group",
                color_discrete_map=COLORS,
                category_orders={"wcat": ["Dry", "Rainy", "Snowy"]},
                text=avg_wrev["est_revenue"].apply(lambda x: f"${x:,.0f}"),
                labels={"est_revenue": "Avg daily e-bike revenue ($)", "wcat": "Weather",
                        "member_casual": "Rider type"},
                title="Avg Daily E-Bike Revenue by Weather Condition",
            )
            fig_wr.update_traces(textposition="outside")
            fig_wr.update_layout(legend=dict(orientation="h", y=1.02, x=1, xanchor="right"))
            st.plotly_chart(fig_wr, use_container_width=True)

            dry_r   = avg_wrev[avg_wrev["wcat"] == "Dry"]["est_revenue"].sum()
            snowy_r = avg_wrev[avg_wrev["wcat"] == "Snowy"]["est_revenue"].sum()
            rainy_r = avg_wrev[avg_wrev["wcat"] == "Rainy"]["est_revenue"].sum()
            if snowy_r > 0:
                st.caption(
                    f"A snowy day costs an estimated **${dry_r - snowy_r:,.0f}** less in e-bike revenue "
                    f"vs. a dry day; a rainy day costs **${dry_r - rainy_r:,.0f}** less. "
                    "Casual riders — who pay the unlock fee — take the largest hit."
                )

        st.divider()

        # Monthly revenue trend (stacked area)
        ebike_df["month"] = ebike_df["trip_date"].dt.to_period("M").dt.to_timestamp()
        monthly_rev = ebike_df.groupby(["month", "member_casual"])["est_revenue"].sum().reset_index()

        fig_mr = px.area(
            monthly_rev, x="month", y="est_revenue", color="member_casual",
            color_discrete_map=COLORS,
            labels={"est_revenue": "Monthly e-bike revenue ($)", "month": "Month",
                    "member_casual": "Rider type"},
            title="Monthly Estimated E-Bike Revenue (Feb 2021+)",
        )
        fig_mr.update_layout(
            legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
            hovermode="x unified",
        )
        st.plotly_chart(fig_mr, use_container_width=True)
        st.caption(
            "Stacked area shows the growing e-bike fleet from 2021 onward. "
            "Casual revenue swings more with seasons; member revenue grows steadily. "
            "**Citibike 2025 pricing applied as a consistent yardstick — not historical revenue.**"
        )

# ── footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "**Source:** Citibike trip data 2013–2026 · Public S3 archives → GCS → BigQuery ETL · "
    "Weather: `nyu-datasets.weather.m_weather_daily_nyc` (Central Park) · "
    "`distance_km_straight_line` = great-circle distance, not actual ride distance · "
    "E-bike revenue uses Citibike 2025 pricing as a yardstick · "
    "Project: `msbai-dwd-csc9720`"
)
