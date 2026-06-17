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

import pandas as pd

TEMP_BINS = [-100, 32, 50, 65, 80, 200]
TEMP_LABELS = ["<32F (freezing)", "32-50F", "50-65F", "65-80F", "80F+"]


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
