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

st.set_page_config(
    page_title="NYC Citibike & Weather Dashboard",
    page_icon="🚲",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── consistent color palette ─────────────────────────────────────────────────
COLORS = {"member": "#1F77B4", "casual": "#FF7F0E"}
SEQ_BLUE = px.colors.sequential.Blues
WEATHER_COLORS = {"Dry": "#2ECC71", "Rainy": "#3498DB", "Snowy": "#9B59B6"}


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

# ── sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/0/04/"
        "Citi_Bike_logo.svg/320px-Citi_Bike_logo.svg.png",
        width=160,
    )
    st.markdown("## Filters")

    min_date, max_date = df["trip_date"].min().date(), df["trip_date"].max().date()
    date_range = st.date_input(
        "Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date
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
    with st.expander("ℹ️ About this dashboard"):
        st.markdown(
            """
**Data sources**
- Citibike trip data (2013–2026) from public S3 archives → GCS → BigQuery
- Weather: Central Park station (`nyu-datasets.weather.m_weather_daily_nyc`)

**How it works**
The ~14 k-row daily table is read **once** on load and cached for 1 hour.
All chart interactions run on local pandas — no BigQuery per click.

**Distance note**
`distance_km_straight_line` is great-circle distance between stations,
*not* actual ride distance. Round trips (same start/end station) = 0 km.

**Project:** `msbai-dwd-csc9720`
"""
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
    st.warning("No data for the selected filters. Adjust the filters in the sidebar.")
    st.stop()

# ── header ────────────────────────────────────────────────────────────────────
st.title("🚲 NYC Citibike Ridership & Weather")
st.caption(
    "Explore how day-to-day weather relates to Citibike ridership across "
    "NYC and Jersey City, 2013–2026.  "
    "Use the sidebar to slice by date, region, and rider type."
)

# ── KPI row ───────────────────────────────────────────────────────────────────
k = T.kpis(fdf)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("🚲 Total trips", f"{k['total_trips']:,}")
c2.metric("📅 Days covered", f"{k['num_days']:,}")
c3.metric("📈 Avg trips / day", f"{k['avg_daily_trips']:,.0f}")
c4.metric("⏱️ Avg trip duration", f"{k['avg_duration_min']:,.1f} min")
c5.metric("📍 Avg straight-line dist.", f"{k['avg_distance_km']:,.2f} km")

st.markdown("---")

# ── tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Ridership & Growth",
    "🌡️ Weather Impact",
    "🔮 Model & Forecast",
    "💰 E-Bike Revenue",
])

# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Ridership & Growth
# ════════════════════════════════════════════════════════════════════════════
with tab1:

    # ── Chart 1: Daily trips over time ───────────────────────────────────
    st.subheader("1. Daily ridership over time")
    daily = T.daily_trips(fdf)

    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=daily["trip_date"], y=daily["num_trips"],
        mode="lines", name="Daily trips",
        line=dict(color="#A8C8FF", width=1), opacity=0.7,
    ))
    fig1.add_trace(go.Scatter(
        x=daily["trip_date"], y=daily["rolling_28d"],
        mode="lines", name="28-day rolling average",
        line=dict(color="#1F77B4", width=2.5),
    ))
    fig1.update_layout(
        xaxis_title="Date", yaxis_title="Trips",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig1, use_container_width=True)

    peak_day = daily.loc[daily["num_trips"].idxmax()]
    trough = daily.loc[daily["num_trips"].idxmin()]
    st.caption(
        f"Ridership swings hugely with the seasons: the busiest day had "
        f"**{int(peak_day['num_trips']):,} trips** ({peak_day['trip_date'].date()}), "
        f"vs. just {int(trough['num_trips']):,} on the quietest day "
        f"({trough['trip_date'].date()}) — roughly a "
        f"**{peak_day['num_trips'] / max(trough['num_trips'], 1):,.0f}x** difference "
        f"between a peak summer day and a winter low."
    )

    st.divider()

    # ── Chart 5: Actual vs. expected (seasonal baseline) ─────────────────
    st.subheader("2. Is it us or the weather? Actual vs. seasonal baseline")
    st.caption(
        "Expected ridership = the average number of trips for that calendar month "
        "across all years in the selection. Large gaps suggest something beyond "
        "ordinary seasonality — a service disruption, an extreme weather event, "
        "or a fleet change."
    )

    monthly = T.actual_vs_expected(fdf)

    fig5 = go.Figure()
    fig5.add_trace(go.Scatter(
        x=monthly["trip_date"], y=monthly["num_trips"],
        mode="lines+markers", name="Actual",
        line=dict(color="#1F77B4", width=2),
        marker=dict(size=4),
    ))
    fig5.add_trace(go.Scatter(
        x=monthly["trip_date"], y=monthly["expected"],
        mode="lines", name="Expected (seasonal baseline)",
        line=dict(color="#FF7F0E", width=2, dash="dot"),
    ))
    fig5.update_layout(
        xaxis_title="Month", yaxis_title="Trips / month",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig5, use_container_width=True)

    worst = monthly.loc[monthly["pct_diff"].idxmin()]
    best = monthly.loc[monthly["pct_diff"].idxmax()]
    st.caption(
        f"Biggest shortfall: **{worst['trip_date'].strftime('%B %Y')}** at "
        f"{worst['pct_diff']:.0f}% below expected "
        f"({int(worst['num_trips']):,} vs {int(worst['expected']):,} trips). "
        f"Biggest surplus: **{best['trip_date'].strftime('%B %Y')}** at "
        f"+{best['pct_diff']:.0f}% above expected "
        f"({int(best['num_trips']):,} vs {int(best['expected']):,} trips)."
    )

    st.divider()

    # ── Chart 10: Year-over-year growth ──────────────────────────────────
    st.subheader("3. Year-over-year growth by region")
    yoy = T.yoy_growth(fdf)
    if not yoy.empty:
        fig10 = px.bar(
            yoy, x="year", y="num_trips", color="system",
            barmode="group",
            color_discrete_map={"NYC": "#1F77B4", "JC": "#FF7F0E"},
            labels={"year": "Year", "num_trips": "Total trips", "system": "Region"},
            text=yoy["num_trips"].apply(lambda x: f"{x/1e6:.1f}M"),
        )
        fig10.update_traces(textposition="outside")
        fig10.update_layout(uniformtext_minsize=8, uniformtext_mode="hide")
        st.plotly_chart(fig10, use_container_width=True)

        nyc = yoy[yoy["system"] == "NYC"].set_index("year")["num_trips"]
        if len(nyc) >= 2:
            first_yr, last_yr = nyc.index.min(), nyc.index.max()
            cagr = ((nyc[last_yr] / nyc[first_yr]) ** (1 / max(last_yr - first_yr, 1)) - 1) * 100
            st.caption(
                f"NYC ridership grew from {nyc[first_yr]/1e6:.1f}M trips in {first_yr} to "
                f"{nyc[last_yr]/1e6:.1f}M in {last_yr} — a **{cagr:.1f}% compound annual growth rate**. "
                f"Years that fall below the trend (e.g. 2020) reflect real disruptions "
                f"that weather alone cannot explain."
            )

    st.divider()

    # ── Chart 12: Weekday vs. weekend ────────────────────────────────────
    st.subheader("4. Weekday vs. weekend — commuters vs. leisure riders")
    ww = T.weekday_vs_weekend(fdf)
    fig12 = px.bar(
        ww, x="day_class", y="avg_trips", color="member_casual",
        barmode="group",
        color_discrete_map=COLORS,
        category_orders={"day_class": ["Weekday", "Weekend"]},
        labels={"day_class": "", "avg_trips": "Avg trips / day", "member_casual": "Rider type"},
        text=ww["avg_trips"].apply(lambda x: f"{int(x):,}"),
    )
    fig12.update_traces(textposition="outside")
    st.plotly_chart(fig12, use_container_width=True)

    try:
        ww_pivot = ww.pivot(index="member_casual", columns="day_class", values="avg_trips")
        if "Weekday" in ww_pivot.columns and "Weekend" in ww_pivot.columns:
            casual_lift = (ww_pivot.loc["casual", "Weekend"] / ww_pivot.loc["casual", "Weekday"] - 1) * 100
            member_drop = (1 - ww_pivot.loc["member", "Weekend"] / ww_pivot.loc["member", "Weekday"]) * 100
            st.caption(
                f"Members ride **{member_drop:.0f}% less** on weekends — they commute on "
                f"weekdays and don't need the bike on Saturday. Casual riders ride "
                f"**{casual_lift:.0f}% more** on weekends — leisure and tourism peak when "
                f"offices are closed. Weekend promotions therefore target the high-margin "
                f"casual segment at exactly the moment they're most active."
            )
    except (KeyError, ValueError):
        pass

    st.divider()

    # ── Chart 9: Day-of-week × month heatmap ─────────────────────────────
    st.subheader("5. When do people ride? Day-of-week × season heatmap")
    heatmap_data = T.dow_month_heatmap(fdf)
    fig9 = px.imshow(
        heatmap_data,
        color_continuous_scale="Blues",
        aspect="auto",
        labels={"x": "Month", "y": "Day of week", "color": "Avg trips / day"},
        text_auto=".0f",
    )
    fig9.update_xaxes(side="top")
    fig9.update_traces(textfont_size=11)
    st.plotly_chart(fig9, use_container_width=True)

    peak_dow = heatmap_data.mean(axis=1).idxmax()
    peak_month = heatmap_data.mean(axis=0).idxmax()
    st.caption(
        f"Ridership peaks on **{peak_dow}s in {peak_month}** — the intersection of the "
        f"busiest day of the week and the busiest season. Weekends in summer are dominated "
        f"by casual/leisure riders; weekday patterns hold steady year-round from members "
        f"commuting regardless of season."
    )

    st.divider()

    # ── Chart 11: Fleet shift ─────────────────────────────────────────────
    st.subheader("6. Fleet shift: classic → electric bikes (2021 onward)")
    fleet = T.fleet_shift(fdf)
    if fleet.empty:
        st.info("No electric bike data in the current selection. Extend the date range to include 2021+.")
    else:
        fig11 = px.line(
            fleet, x="trip_date", y="pct_electric", color="system",
            color_discrete_map={"NYC": "#1F77B4", "JC": "#FF7F0E"},
            labels={"trip_date": "Month", "pct_electric": "E-bike share of trips (%)", "system": "Region"},
        )
        fig11.add_hline(y=50, line_dash="dash", line_color="gray",
                        annotation_text="50% threshold", annotation_position="right")
        fig11.update_layout(hovermode="x unified")
        st.plotly_chart(fig11, use_container_width=True)

        latest_pct = fleet.groupby("system")["pct_electric"].last()
        caption_parts = [f"{sys}: {pct:.0f}%" for sys, pct in latest_pct.items()]
        st.caption(
            f"E-bikes have grown from 0% to {', '.join(caption_parts)} of all trips "
            f"since Feb 2021. The shift matters for revenue: e-bike trips carry a "
            f"per-minute charge that classic trips (for members) do not."
        )


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Weather Impact
# ════════════════════════════════════════════════════════════════════════════
with tab2:

    # ── Chart 2: Ridership vs. temperature ───────────────────────────────
    st.subheader("1. Ridership vs. temperature")
    temp_df = fdf.dropna(subset=["tavg_f"]).copy()

    fig2 = px.scatter(
        temp_df, x="tavg_f", y="num_trips",
        color="member_casual",
        color_discrete_map=COLORS,
        opacity=0.35,
        trendline="lowess",
        trendline_options={"frac": 0.3},
        labels={"tavg_f": "Average daily temperature (°F)", "num_trips": "Trips", "member_casual": "Rider type"},
    )
    fig2.update_traces(marker=dict(size=4))
    fig2.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    st.plotly_chart(fig2, use_container_width=True)

    corr = T.temp_correlation(fdf)
    corr_text = ", ".join(f"{k}: r={v:.2f}" for k, v in corr.items())
    st.caption(
        f"Warmer days mean more trips for both rider types, but **casual riders are "
        f"far more weather-sensitive than members** (temperature–ridership correlation — "
        f"{corr_text}). On the coldest days casual ridership nearly flatlines, while "
        f"members keep commuting year-round. Trendlines show LOWESS smoothed fit."
    )

    st.divider()

    # ── Chart 3: Casual share by temperature band ─────────────────────────
    st.subheader("2. Casual-rider share by temperature band")
    share = T.casual_share_by_band(fdf)

    fig3 = px.bar(
        share, x="temp_band", y="pct", color="member_casual",
        barmode="stack",
        color_discrete_map=COLORS,
        category_orders={"temp_band": T.TEMP_LABELS},
        labels={"temp_band": "Average daily temperature", "pct": "Share of trips (%)", "member_casual": "Rider type"},
    )
    fig3.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    st.plotly_chart(fig3, use_container_width=True)

    if "casual" in share["member_casual"].unique():
        casual_share = share[share["member_casual"] == "casual"].set_index("temp_band")["pct"]
        cold_val = casual_share.get("<32F (freezing)", 0)
        hot_val = casual_share.get("80F+", 0)
        if cold_val > 0 and hot_val > 0:
            st.caption(
                f"Casual riders make up only **{cold_val:.1f}%** of trips on freezing days (<32°F), "
                f"but **{hot_val:.1f}%** on hot days (80°F+) — nearly a "
                f"**{hot_val / max(cold_val, 0.1):.0f}x jump**. "
                f"Warm-weather promotions targeting casual/tourist riders would land "
                f"on exactly the days those riders are already most active."
            )

    st.divider()

    # ── Chart 4: Rain/snow impact ─────────────────────────────────────────
    st.subheader("3. Impact of rain and snow on daily ridership")
    avg_by_type = T.avg_by_daytype(fdf)

    fig4 = px.bar(
        avg_by_type, x="day_type", y="avg_trips", color="member_casual",
        barmode="group",
        color_discrete_map=COLORS,
        category_orders={"day_type": ["Dry", "Rainy", "Snowy"]},
        text=avg_by_type["avg_trips"].apply(lambda x: f"{int(x):,}"),
        labels={"day_type": "Day type", "avg_trips": "Avg trips / day", "member_casual": "Rider type"},
    )
    fig4.update_traces(textposition="outside")
    fig4.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    st.plotly_chart(fig4, use_container_width=True)

    try:
        pivot = avg_by_type.pivot(index="member_casual", columns="day_type", values="avg_trips")
        if "Dry" in pivot.columns and "Snowy" in pivot.columns:
            drops = []
            for rider in pivot.index:
                dry, snowy = pivot.loc[rider, "Dry"], pivot.loc[rider, "Snowy"]
                if pd.notna(dry) and pd.notna(snowy) and dry > 0:
                    pct_drop = (1 - snowy / dry) * 100
                    drops.append(f"**{rider}** ridership drops {pct_drop:.0f}%")
            if drops:
                st.caption(
                    "Compared to a dry day, on snowy days: " + "; ".join(drops) + ". "
                    "Bad weather thins the promotable casual segment first."
                )
    except (KeyError, ValueError):
        pass

    st.divider()

    # ── Temperature distribution comparison ──────────────────────────────
    st.subheader("4. Temperature distribution: riding days vs. all days")
    temp_all = fdf.dropna(subset=["tavg_f"])
    daily_temp = temp_all.groupby("trip_date").agg(
        num_trips=("num_trips", "sum"), tavg_f=("tavg_f", "first")
    ).reset_index()
    median_temp = daily_temp["tavg_f"].median()

    fig_td = go.Figure()
    fig_td.add_trace(go.Histogram(
        x=daily_temp["tavg_f"], name="All days",
        opacity=0.5, nbinsx=30, marker_color="#636EFA",
    ))
    fig_td.add_vline(x=median_temp, line_dash="dash", line_color="gray",
                     annotation_text=f"Median {median_temp:.0f}°F", annotation_position="top right")
    fig_td.update_layout(
        xaxis_title="Average daily temperature (°F)",
        yaxis_title="Number of days",
        bargap=0.05,
    )
    st.plotly_chart(fig_td, use_container_width=True)
    pct_warm = (daily_temp["tavg_f"] >= 65).mean() * 100
    st.caption(
        f"**{pct_warm:.0f}% of days in the selection are 65°F+** — the range where "
        f"casual ridership climbs sharply. The temperature distribution sets the "
        f"ceiling for how often weather-driven promotions can be effective."
    )


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Model & Forecast (Stretch 1 & 2)
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.info("Charts in this tab use the full historical dataset — not affected by sidebar filters.", icon="ℹ️")

    # ── Stretch 1: Weather-adjusted ridership model ───────────────────────
    st.subheader("1. Weather-Adjusted Ridership Model")
    st.markdown(
        "A gradient-boosting model learns what ridership *should* look like given the weather, "
        "day of week, and season. Days where actual ridership falls far below the model's "
        "expectation signal an **operational problem** — not just bad weather."
    )

    @st.cache_data(ttl=3600)
    def get_model_results(_df):
        return build_weather_model(_df)

    with st.spinner("Fitting weather model (runs once, then cached)…"):
        model, mape_pct, test_df = get_model_results(df)

    residuals_df = compute_residuals(df, model)
    std_resid = residuals_df["residual_pct"].std()
    residuals_df["outlier"] = residuals_df["residual_pct"].abs() > 2 * std_resid

    # model accuracy badge
    col_a, col_b = st.columns(2)
    col_a.metric("Model out-of-sample MAPE (2024+)", f"{mape_pct:.1f}%",
                 help="Mean Absolute Percentage Error on held-out 2024+ data the model never saw during training.")
    n_outliers = residuals_df["outlier"].sum()
    col_b.metric("Flagged anomaly days", f"{n_outliers:,}",
                 help="Days where |actual − expected| > 2 standard deviations — worth investigating.")

    # actual vs expected
    fig6a = go.Figure()
    fig6a.add_trace(go.Scatter(
        x=residuals_df["trip_date"], y=residuals_df["num_trips"],
        mode="lines", name="Actual",
        line=dict(color="#1F77B4", width=1.5),
    ))
    fig6a.add_trace(go.Scatter(
        x=residuals_df["trip_date"], y=residuals_df["predicted_trips"],
        mode="lines", name="Model expected",
        line=dict(color="#FF7F0E", width=1.5, dash="dot"),
    ))
    fig6a.update_layout(
        title="Actual vs. Weather-Expected Daily Trips",
        xaxis_title="Date", yaxis_title="Trips",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig6a, use_container_width=True)

    # residuals bar
    colors_resid = ["#E74C3C" if o else "#BDC3C7" for o in residuals_df["outlier"]]
    fig6b = go.Figure()
    fig6b.add_trace(go.Bar(
        x=residuals_df["trip_date"], y=residuals_df["residual_pct"],
        marker_color=colors_resid, name="Residual %",
    ))
    fig6b.add_hline(y=0, line_dash="dash", line_color="gray")
    fig6b.add_hline(y=2*std_resid, line_dash="dot", line_color="#E74C3C",
                    annotation_text="+2σ", annotation_position="right")
    fig6b.add_hline(y=-2*std_resid, line_dash="dot", line_color="#E74C3C",
                    annotation_text="−2σ", annotation_position="right")
    fig6b.update_layout(
        title="Residual % — Actual vs. Expected (anomalies in red)",
        xaxis_title="Date", yaxis_title="Residual %",
    )
    st.plotly_chart(fig6b, use_container_width=True)
    st.caption(
        f"Model: GradientBoostingRegressor trained on pre-2024 data; "
        f"**test-set MAPE = {mape_pct:.1f}%** on 2024+ held-out data. "
        "Days flagged red (|residual| > 2σ) diverge more than weather/calendar patterns "
        "can explain — likely operational events, system outages, or promotions. "
        "Uses full unfiltered dataset (all systems and rider types)."
    )

    # top anomaly days table
    st.markdown("**Top 10 largest anomaly days**")
    top_anomalies = (
        residuals_df[residuals_df["outlier"]]
        .assign(abs_resid=lambda d: d["residual_pct"].abs())
        .nlargest(10, "abs_resid")[["trip_date", "num_trips", "predicted_trips", "residual_pct", "tavg_f"]]
        .rename(columns={
            "trip_date": "Date", "num_trips": "Actual trips",
            "predicted_trips": "Expected trips", "residual_pct": "Residual %",
            "tavg_f": "Avg temp (°F)",
        })
    )
    top_anomalies["Date"] = top_anomalies["Date"].dt.strftime("%Y-%m-%d")
    top_anomalies["Actual trips"] = top_anomalies["Actual trips"].apply(lambda x: f"{int(x):,}")
    top_anomalies["Expected trips"] = top_anomalies["Expected trips"].apply(lambda x: f"{int(x):,}")
    top_anomalies["Residual %"] = top_anomalies["Residual %"].apply(lambda x: f"{x:+.1f}%")
    top_anomalies["Avg temp (°F)"] = top_anomalies["Avg temp (°F)"].apply(lambda x: f"{x:.1f}°F" if pd.notna(x) else "—")
    st.dataframe(top_anomalies, use_container_width=True, hide_index=True)

    st.divider()

    # ── Stretch 2: 7-day forecast ─────────────────────────────────────────
    st.subheader("2. 7-Day Ridership Forecast")
    st.markdown(
        "The same model applied to this week's weather forecast from "
        "[Open-Meteo](https://open-meteo.com/) (free API, no key required)."
    )

    @st.cache_data(ttl=3600)
    def get_forecast():
        return fetch_forecast()

    forecast_raw = get_forecast()
    if forecast_raw is None:
        st.warning("Forecast unavailable — could not reach Open-Meteo API. Try again later.")
    else:
        forecast_pred = predict_forecast(model, forecast_raw)
        day_labels = forecast_pred["trip_date"].dt.strftime("%a\n%b %d")

        fig7 = go.Figure()
        fig7.add_trace(go.Bar(
            x=day_labels,
            y=forecast_pred["predicted_trips"].round().astype(int),
            marker_color=[
                "#9B59B6" if row["snow_in"] > 0.1
                else "#3498DB" if row["prcp_in"] > 0.1
                else "#2ECC71"
                for _, row in forecast_raw.iterrows()
            ],
            text=forecast_pred["predicted_trips"].round().astype(int).apply(lambda x: f"{x:,}"),
            textposition="outside",
            name="Predicted trips",
        ))
        fig7.update_layout(
            title="Predicted Daily Trips — Next 7 Days",
            xaxis_title="Date", yaxis_title="Predicted trips",
            showlegend=False,
        )
        st.plotly_chart(fig7, use_container_width=True)

        # temperature + precipitation mini-table
        fc_display = forecast_pred.copy()
        fc_display["Date"] = fc_display["trip_date"].dt.strftime("%A, %b %d")
        fc_display["Forecast temp (°F)"] = forecast_raw["tavg_f"].round(1).apply(lambda x: f"{x:.0f}°F")
        fc_display["Precip (in)"] = forecast_raw["prcp_in"].round(2).apply(lambda x: f"{x:.2f}\"")
        fc_display["Snow (in)"] = forecast_raw["snow_in"].round(2).apply(lambda x: f"{x:.2f}\"")
        fc_display["Predicted trips"] = fc_display["predicted_trips"].round().astype(int).apply(lambda x: f"{x:,}")
        st.dataframe(
            fc_display[["Date", "Forecast temp (°F)", "Precip (in)", "Snow (in)", "Predicted trips"]],
            use_container_width=True, hide_index=True,
        )

        st.caption(
            f"Bar color: 🟢 Dry, 🔵 Rainy, 🟣 Snowy (based on Open-Meteo forecast). "
            f"Model accuracy on held-out 2024+ data: **{mape_pct:.1f}% MAPE**. "
            "All regions and rider types combined. Forecast weather adds uncertainty "
            "beyond the model's training-set error."
        )


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — E-Bike Revenue (Stretch 3)
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("E-Bike Revenue Estimate (Feb 2021 onward)")
    st.markdown(
        """
**Pricing (Citibike 2025 rates applied as a historical yardstick):**
- Casual e-bike: \$1.00 unlock + \$0.26/min
- Member e-bike: \$0.17/min (membership covers access, no unlock fee)

*Scope: Feb 2021+ rows where `num_electric_trips > 0`.
Revenue figures are estimates — avg duration is group-level, not e-bike-specific.*
"""
    )

    ebike_df = ebike_revenue(fdf)

    if ebike_df.empty:
        st.info("No e-bike data available for the current filter selection (requires 2021-02+ data).")
    else:
        total_rev = ebike_df["est_revenue"].sum()
        total_etrips = int(ebike_df["num_electric_trips"].sum())
        avg_rev_per_trip = total_rev / max(total_etrips, 1)

        k1, k2, k3 = st.columns(3)
        k1.metric("💰 Total est. e-bike revenue", f"${total_rev:,.0f}")
        k2.metric("⚡ Total e-bike trips", f"{total_etrips:,}")
        k3.metric("💵 Avg revenue per e-bike trip", f"${avg_rev_per_trip:.2f}")

        st.divider()

        # Weather breakdown
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
                return "Dry"

            ebike_weather["weather_cat"] = ebike_weather.apply(weather_cat, axis=1)
            rev_by_day = ebike_df.groupby(["trip_date", "member_casual"])["est_revenue"].sum().reset_index()
            ebike_weather_daily = ebike_weather.groupby(["trip_date", "member_casual"]).agg(
                prcp_in=("prcp_in", "first"), snow_in=("snow_in", "first")
            ).reset_index()
            ebike_weather_daily["weather_cat"] = ebike_weather_daily.apply(weather_cat, axis=1)
            merged_w = ebike_weather_daily.merge(rev_by_day, on=["trip_date", "member_casual"], how="left")
            avg_by_weather = merged_w.groupby(["weather_cat", "member_casual"])["est_revenue"].mean().reset_index()

            fig8a = px.bar(
                avg_by_weather, x="weather_cat", y="est_revenue", color="member_casual",
                barmode="group",
                color_discrete_map=COLORS,
                category_orders={"weather_cat": ["Dry", "Rainy", "Snowy"]},
                text=avg_by_weather["est_revenue"].apply(lambda x: f"${x:,.0f}"),
                labels={
                    "est_revenue": "Avg daily e-bike revenue ($)",
                    "weather_cat": "Weather",
                    "member_casual": "Rider type",
                },
                title="Avg Daily E-Bike Revenue by Weather Condition and Rider Type",
            )
            fig8a.update_traces(textposition="outside")
            fig8a.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig8a, use_container_width=True)

            dry_rev = avg_by_weather[avg_by_weather["weather_cat"] == "Dry"]["est_revenue"].sum()
            rainy_rev = avg_by_weather[avg_by_weather["weather_cat"] == "Rainy"]["est_revenue"].sum()
            snowy_rev = avg_by_weather[avg_by_weather["weather_cat"] == "Snowy"]["est_revenue"].sum()
            if snowy_rev > 0 and dry_rev > 0:
                st.caption(
                    f"A snowy day costs an estimated **${dry_rev - snowy_rev:,.0f} less** in e-bike "
                    f"revenue vs. a dry day; a rainy day costs **${dry_rev - rainy_rev:,.0f} less**. "
                    "Casual riders — who pay the unlock fee — take the largest hit on bad-weather days."
                )

        st.divider()

        # Monthly revenue trend
        ebike_df2 = ebike_df.copy()
        ebike_df2["month"] = ebike_df2["trip_date"].dt.to_period("M").dt.to_timestamp()
        monthly_rev = ebike_df2.groupby(["month", "member_casual"])["est_revenue"].sum().reset_index()

        fig8b = px.area(
            monthly_rev, x="month", y="est_revenue", color="member_casual",
            color_discrete_map=COLORS,
            labels={"est_revenue": "Monthly e-bike revenue ($)", "month": "Month", "member_casual": "Rider type"},
            title="Monthly Estimated E-Bike Revenue (Feb 2021+)",
        )
        fig8b.update_layout(
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
        )
        st.plotly_chart(fig8b, use_container_width=True)
        st.caption(
            "Stacked area shows the growing e-bike revenue as the fleet expanded from 2021 onward. "
            "Member revenue grows more steadily (commuter demand); casual revenue shows stronger "
            "seasonal swings, peaking in summer. "
            "**2025 pricing applied as a consistent yardstick — not reconstructed historical revenue.**"
        )

# ── footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "**Data:** Citibike trip data (2013–2026) from public S3 archives via GCS → BigQuery ETL. "
    "**Weather:** `nyu-datasets.weather.m_weather_daily_nyc` (Central Park). "
    "`distance_km_straight_line` = great-circle distance, not actual ride distance. "
    "E-bike revenue uses Citibike 2025 pricing as a yardstick. "
    "**Project:** `msbai-dwd-csc9720` | "
    "[GitHub](https://github.com/caischen7/msbai-dwd-csc9720)"
)
