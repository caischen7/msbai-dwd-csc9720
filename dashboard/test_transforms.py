"""Offline correctness check for the dashboard's arithmetic.

No BigQuery, no Streamlit, no network: build a tiny synthetic daily table with
values chosen so every aggregate can be worked out by hand, then assert each
transform reproduces the hand-computed answer. This is the dashboard half of
the project's Correctness target — it proves the charts do the math we claim.
(The other half — that the input table matches the BigQuery daily table — is a
separate live check documented in docs/DASHBOARD_DECISIONS.md.)

Run:  python3 dashboard/test_transforms.py
"""

import math

import pandas as pd

import transforms as T

# 4 days x (1 system) x (2 rider types) = 8 rows, all numbers chosen round.
# Columns mirror citibike.daily_summary_with_weather.
ROWS = [
    # date,        system, rider,    trips, dur, dist, tavg, rainy, snowy
    ("2020-01-15", "NYC", "member", 100, 12, 1.0, 20, False, True),   # cold, snowy
    ("2020-01-15", "NYC", "casual", 10, 20, 2.0, 20, False, True),
    ("2020-01-16", "NYC", "member", 120, 12, 1.0, 40, True, False),   # cool, rainy
    ("2020-01-16", "NYC", "casual", 30, 20, 2.0, 40, True, False),
    ("2020-07-15", "NYC", "member", 200, 12, 1.0, 85, False, False),  # hot, dry
    ("2020-07-15", "NYC", "casual", 150, 20, 2.0, 85, False, False),
    ("2021-07-15", "NYC", "member", 180, 12, 1.0, 75, True, False),   # warm, rainy
    ("2021-07-15", "NYC", "casual", 120, 20, 2.0, 75, True, False),
]
COLS = [
    "trip_date", "system", "member_casual", "num_trips",
    "avg_trip_duration_minutes", "avg_distance_km_straight_line",
    "tavg_f", "is_rainy", "is_snowy",
]


def make_df():
    df = pd.DataFrame(ROWS, columns=COLS)
    df["trip_date"] = pd.to_datetime(df["trip_date"])
    return df


def close(a, b, tol=1e-6):
    assert math.isclose(a, b, rel_tol=0, abs_tol=tol), f"{a} != {b}"


def test_kpis():
    k = T.kpis(make_df())
    assert k["total_trips"] == 910                      # 100+10+120+30+200+150+180+120
    assert k["num_days"] == 4
    close(k["avg_daily_trips"], 910 / 4)                # 227.5
    # duration weighted by trips: (600 member*12 + 310 casual*20) / 910
    close(k["avg_duration_min"], (600 * 12 + 310 * 20) / 910, tol=1e-9)
    close(k["avg_distance_km"], (600 * 1.0 + 310 * 2.0) / 910, tol=1e-9)


def test_daily_trips():
    daily = T.daily_trips(make_df()).set_index("trip_date")["num_trips"]
    close(daily[pd.Timestamp("2020-01-15")], 110)       # 100 + 10
    close(daily[pd.Timestamp("2020-01-16")], 150)
    close(daily[pd.Timestamp("2020-07-15")], 350)
    close(daily[pd.Timestamp("2021-07-15")], 300)
    assert daily.max() == 350 and daily.min() == 110


def test_temp_correlation():
    corr = T.temp_correlation(make_df())
    close(corr["member"], 0.99437, tol=1e-4)
    close(corr["casual"], 0.98752, tol=1e-4)


def test_casual_share_by_band():
    share = T.casual_share_by_band(make_df())
    casual = share[share["member_casual"] == "casual"].set_index("temp_band")["pct"]
    close(casual["<32F (freezing)"], 10 / 110 * 100, tol=1e-6)   # 9.0909
    close(casual["32-50F"], 30 / 150 * 100, tol=1e-6)            # 20.0
    close(casual["65-80F"], 120 / 300 * 100, tol=1e-6)           # 40.0
    close(casual["80F+"], 150 / 350 * 100, tol=1e-6)             # 42.857


def test_avg_by_daytype():
    avg = T.avg_by_daytype(make_df()).set_index(["day_type", "member_casual"])["avg_trips"]
    close(avg[("Snowy", "member")], 100)        # one snowy day, 100 trips
    close(avg[("Dry", "member")], 200)
    close(avg[("Rainy", "member")], 150)        # (120 + 180) / 2 distinct days
    close(avg[("Rainy", "casual")], 75)         # (30 + 120) / 2


def test_actual_vs_expected():
    m = T.actual_vs_expected(make_df())
    m = m.set_index(m["trip_date"].dt.strftime("%Y-%m"))
    # month-of-year 7 appears twice (2020-07=350, 2021-07=300) -> expected 325
    close(m.loc["2020-07", "expected"], 325)
    close(m.loc["2021-07", "expected"], 325)
    close(m.loc["2020-01", "expected"], 260)   # only one January -> equals itself
    close(m.loc["2020-07", "pct_diff"], (350 - 325) / 325 * 100, tol=1e-6)
    close(m.loc["2021-07", "pct_diff"], (300 - 325) / 325 * 100, tol=1e-6)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
