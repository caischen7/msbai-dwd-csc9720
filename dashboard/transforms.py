"""Pure data transforms for the dashboard.

Every function here takes a pandas DataFrame (the cached daily table) and
returns a DataFrame or scalars — no Streamlit, no BigQuery, no plotting. That
separation is deliberate: it lets test_transforms.py feed in synthetic data
with hand-computed expected answers and assert the dashboard's arithmetic is
correct, without needing cloud credentials or a running Streamlit server.

The dashboard's input columns (from citibike.daily_summary_with_weather):
  trip_date, system, member_casual, num_trips, num_classic_trips,
  num_electric_trips, avg_trip_duration_minutes, median_trip_duration_minutes,
  avg_distance_km_straight_line, tavg_f, tmax_f, tmin_f, prcp_in, snow_in,
  is_rainy, is_snowy
"""

import math
import requests
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_percentage_error

TEMP_BINS = [-100, 32, 50, 65, 80, 200]
TEMP_LABELS = ["<32F (freezing)", "32-50F", "50-65F", "65-80F", "80F+"]

EBIKE_RATE_CASUAL_PER_MIN = 0.26
EBIKE_RATE_MEMBER_PER_MIN = 0.17
EBIKE_UNLOCK_CASUAL = 1.00


def kpis(df):
    """Headline numbers. Duration and distance are weighted by num_trips so a
    day with 100k trips counts 100x more than a day with 1k toward the average
    — a plain mean of the per-group averages would over-weight quiet days."""
    total_trips = int(df["num_trips"].sum())
    num_days = int(df["trip_date"].nunique())
    avg_daily_trips = total_trips / num_days if num_days else 0.0
    if total_trips:
        avg_duration = (df["avg_trip_duration_minutes"] * df["num_trips"]).sum() / total_trips
        avg_distance = (df["avg_distance_km_straight_line"] * df["num_trips"]).sum() / total_trips
    else:
        avg_duration = avg_distance = 0.0
    return {
        "total_trips": total_trips,
        "num_days": num_days,
        "avg_daily_trips": avg_daily_trips,
        "avg_duration_min": avg_duration,
        "avg_distance_km": avg_distance,
    }


def daily_trips(df):
    """One row per calendar day (summed across the selected systems/rider
    types), with a 28-day rolling average for the trend line."""
    daily = df.groupby("trip_date", as_index=False)["num_trips"].sum().sort_values("trip_date")
    daily["rolling_28d"] = daily["num_trips"].rolling(28, min_periods=7).mean()
    return daily


def temp_correlation(df):
    """Pearson correlation between daily avg temperature and daily trips,
    computed separately per rider type. Returns a Series indexed by rider type.
    Days with no weather match (NaN tavg_f) are dropped first."""
    t = df.dropna(subset=["tavg_f"])
    return (
        t.groupby("member_casual")[["tavg_f", "num_trips"]]
        .apply(lambda g: g["tavg_f"].corr(g["num_trips"]))
    )


def casual_share_by_band(df):
    """Share (%) of trips contributed by each rider type within each
    temperature band. Returns long-form: temp_band, member_casual, num_trips, pct."""
    b = df.dropna(subset=["tavg_f"]).copy()
    b["temp_band"] = pd.cut(b["tavg_f"], bins=TEMP_BINS, labels=TEMP_LABELS)
    share = (
        b.groupby(["temp_band", "member_casual"], observed=True)["num_trips"]
        .sum()
        .reset_index()
    )
    totals = share.groupby("temp_band", observed=True)["num_trips"].transform("sum")
    share["pct"] = share["num_trips"] / totals * 100
    return share


def day_type_series(df):
    """Label each row Dry / Rainy / Snowy (snow takes precedence over rain)."""
    out = df.dropna(subset=["is_rainy", "is_snowy"]).copy()
    out["day_type"] = "Dry"
    out.loc[out["is_rainy"].astype(bool), "day_type"] = "Rainy"
    out.loc[out["is_snowy"].astype(bool), "day_type"] = "Snowy"
    return out


def avg_by_daytype(df):
    """Average trips PER DAY of each (day_type, rider type). Total trips of that
    combination divided by the number of distinct days of that type — not a
    mean of daily rows — so the bars are comparable across day types that occur
    with very different frequencies."""
    w = day_type_series(df)
    grp = w.groupby(["day_type", "member_casual"])
    avg = (grp["num_trips"].sum() / grp["trip_date"].nunique()).reset_index(name="avg_trips")
    return avg


def actual_vs_expected(df):
    """Monthly actual trips vs. a seasonal baseline. 'Expected' for a given
    month is the mean of all months sharing its calendar month-of-year within
    the current selection — a naive deseasonalization. pct_diff is the signed
    gap that the chart flags as 'not explained by ordinary seasonality'."""
    tmp = df.copy()
    tmp["month"] = tmp["trip_date"].dt.to_period("M")
    monthly = tmp.groupby("month", as_index=False)["num_trips"].sum()
    monthly["trip_date"] = monthly["month"].dt.to_timestamp()
    monthly["month_num"] = monthly["trip_date"].dt.month
    monthly["expected"] = monthly.groupby("month_num")["num_trips"].transform("mean")
    monthly["pct_diff"] = (monthly["num_trips"] - monthly["expected"]) / monthly["expected"] * 100
    return monthly


def _daily_totals(df):
    """Aggregate df to one row per date with weather cols."""
    weather_cols = [c for c in ["tavg_f", "prcp_in", "snow_in"] if c in df.columns]
    agg = {
        "num_trips": "sum",
        "num_electric_trips": "sum",
        "avg_trip_duration_minutes": "mean",
    }
    for w in weather_cols:
        agg[w] = "first"
    daily = df.groupby("trip_date").agg(agg).reset_index()
    return daily


def _make_features(daily):
    """Build feature matrix from daily df."""
    d = daily.copy()
    d["trip_date"] = pd.to_datetime(d["trip_date"])
    d["dow"] = d["trip_date"].dt.dayofweek
    d["month"] = d["trip_date"].dt.month
    d["year"] = d["trip_date"].dt.year
    d["dow_sin"] = np.sin(2 * math.pi * d["dow"] / 7)
    d["dow_cos"] = np.cos(2 * math.pi * d["dow"] / 7)
    d["month_sin"] = np.sin(2 * math.pi * d["month"] / 12)
    d["month_cos"] = np.cos(2 * math.pi * d["month"] / 12)
    feature_cols = ["tavg_f", "prcp_in", "snow_in", "dow_sin", "dow_cos", "month_sin", "month_cos", "year"]
    # drop rows missing any feature
    d = d.dropna(subset=feature_cols + ["num_trips"])
    return d, feature_cols


def build_weather_model(df):
    """Train GradientBoostingRegressor on pre-2024 data, test on 2024+.
    Returns (model, mape_pct, test_df)."""
    daily = _daily_totals(df)
    daily, feature_cols = _make_features(daily)
    daily["trip_date"] = pd.to_datetime(daily["trip_date"])
    train = daily[daily["trip_date"] < "2024-01-01"]
    test = daily[daily["trip_date"] >= "2024-01-01"]
    X_train = train[feature_cols].values
    y_train = train["num_trips"].values
    X_test = test[feature_cols].values
    y_test = test["num_trips"].values
    model = GradientBoostingRegressor(n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42)
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    mape_pct = mean_absolute_percentage_error(y_test, y_pred) * 100
    test_df = test.copy()
    test_df["predicted_trips"] = y_pred
    return model, mape_pct, test_df


def compute_residuals(df, model):
    """Returns daily df with predicted_trips and residual_pct columns."""
    daily = _daily_totals(df)
    daily, feature_cols = _make_features(daily)
    daily["trip_date"] = pd.to_datetime(daily["trip_date"])
    X = daily[feature_cols].values
    daily["predicted_trips"] = model.predict(X)
    daily["residual_pct"] = (daily["num_trips"] - daily["predicted_trips"]) / daily["predicted_trips"] * 100
    return daily


def fetch_forecast():
    """Call Open-Meteo API for 7-day NYC forecast. Returns DataFrame or None."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=40.7128&longitude=-74.0060"
        "&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
        "precipitation_sum,snowfall_sum"
        "&temperature_unit=fahrenheit"
        "&precipitation_unit=inch"
        "&timezone=America%2FNew_York"
        "&forecast_days=7"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()["daily"]
        fc = pd.DataFrame({
            "trip_date": pd.to_datetime(data["time"]),
            "tavg_f": data["temperature_2m_mean"],
            "prcp_in": data["precipitation_sum"],
            "snow_in": data["snowfall_sum"],
        })
        return fc
    except Exception:
        return None


def predict_forecast(model, forecast_df):
    """Apply model to forecast_df. Returns forecast_df with predicted_trips column."""
    d = forecast_df.copy()
    d["trip_date"] = pd.to_datetime(d["trip_date"])
    d["dow"] = d["trip_date"].dt.dayofweek
    d["month"] = d["trip_date"].dt.month
    d["year"] = d["trip_date"].dt.year
    d["dow_sin"] = np.sin(2 * math.pi * d["dow"] / 7)
    d["dow_cos"] = np.cos(2 * math.pi * d["dow"] / 7)
    d["month_sin"] = np.sin(2 * math.pi * d["month"] / 12)
    d["month_cos"] = np.cos(2 * math.pi * d["month"] / 12)
    feature_cols = ["tavg_f", "prcp_in", "snow_in", "dow_sin", "dow_cos", "month_sin", "month_cos", "year"]
    d = d.dropna(subset=feature_cols)
    d["predicted_trips"] = model.predict(d[feature_cols].values)
    return d


def ebike_revenue(df):
    """Compute estimated e-bike revenue. Returns long-form df with trip_date, member_casual, est_revenue."""
    scope = df[
        (pd.to_datetime(df["trip_date"]) >= pd.Timestamp("2021-02-01"))
        & (df["num_electric_trips"] > 0)
    ].copy()
    scope["trip_date"] = pd.to_datetime(scope["trip_date"])
    casual_mask = scope["member_casual"] == "casual"
    scope["est_revenue"] = 0.0
    scope.loc[casual_mask, "est_revenue"] = (
        EBIKE_UNLOCK_CASUAL * scope.loc[casual_mask, "num_electric_trips"]
        + EBIKE_RATE_CASUAL_PER_MIN * scope.loc[casual_mask, "avg_trip_duration_minutes"] * scope.loc[casual_mask, "num_electric_trips"]
    )
    scope.loc[~casual_mask, "est_revenue"] = (
        EBIKE_RATE_MEMBER_PER_MIN * scope.loc[~casual_mask, "avg_trip_duration_minutes"] * scope.loc[~casual_mask, "num_electric_trips"]
    )
    return scope[["trip_date", "member_casual", "est_revenue", "num_electric_trips", "avg_trip_duration_minutes"]]
