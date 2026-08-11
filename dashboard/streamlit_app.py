from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"
DASHBOARD_CONFIG_PATH = REPO_ROOT / "config" / "dashboard.yaml"

st.set_page_config(page_title="Day 13 AI Observability", layout="wide")


@st.cache_data(ttl=5)
def load_config() -> dict:
    return yaml.safe_load(DASHBOARD_CONFIG_PATH.read_text(encoding="utf-8"))["dashboard"]


def load_records() -> pd.DataFrame:
    if not LOG_PATH.exists():
        return pd.DataFrame()
    rows = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    return df


def panel_by_id(config: dict, panel_id: str) -> dict:
    return next(p for p in config["panels"] if p["id"] == panel_id)


def threshold_status(value: float | None, threshold: dict) -> str:
    if value is None:
        return "N/A"
    op = threshold["operator"]
    limit = threshold["value"]
    ok = value <= limit if op == "lte" else value >= limit
    return "OK" if ok else "VI PHAM SLO"


config = load_config()
df_all = load_records()

st.title(config["title"])

time_range_minutes = config["time_range_minutes"]
cutoff = datetime.now(timezone.utc) - timedelta(minutes=time_range_minutes)

st.caption(
    f"Time range: {time_range_minutes} phut gan nhat  |  "
    f"Refresh: {config['refresh_seconds']}s  |  "
    f"Now: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
)

if df_all.empty:
    st.warning("Chua co data/logs.jsonl. Chay API va load_test.py truoc.")
    st.stop()

df = df_all[df_all["ts"] >= cutoff].copy()
if df.empty:
    st.warning(f"Khong co log nao trong {time_range_minutes} phut gan nhat.")
    st.stop()

requests = df[df["event"] == "request_received"]
responses = df[df["event"] == "response_sent"]
failures = df[df["event"] == "request_failed"]

col1, col2, col3 = st.columns(3)
col4, col5, col6 = st.columns(3)

# ---- 1. Latency percentiles ----
with col1:
    p_latency = panel_by_id(config, "latency")
    st.subheader(f"{p_latency['title']} ({p_latency['unit']})")
    if not responses.empty:
        lat = responses["latency_ms"].astype(float)
        p50, p95, p99 = lat.quantile([0.5, 0.95, 0.99])
        thr = p_latency["threshold"]
        status = threshold_status(p95, thr)
        st.metric("P95 latency", f"{p95:.0f} ms", delta=status)
        st.write(f"P50: {p50:.0f} ms | P95: {p95:.0f} ms | P99: {p99:.0f} ms")
        st.caption(f"SLO: P95 {thr['operator']} {thr['value']} ms")
        chart_df = responses[["ts", "latency_ms"]].sort_values("ts")
        line = alt.Chart(chart_df).mark_line().encode(x="ts:T", y="latency_ms:Q")
        rule = alt.Chart(pd.DataFrame({"y": [thr["value"]]})).mark_rule(color="red", strokeDash=[4, 4]).encode(y="y")
        st.altair_chart(line + rule, use_container_width=True)
    else:
        st.info("Chua co response_sent event.")

# ---- 2. Traffic ----
with col2:
    p_traffic = panel_by_id(config, "traffic")
    st.subheader(f"{p_traffic['title']} ({p_traffic['unit']})")
    if not requests.empty:
        per_min = requests.set_index("ts").resample("1min").size().reset_index(name="count")
        thr = p_traffic["threshold"]
        rate = per_min["count"].iloc[-1] if not per_min.empty else 0
        st.metric("Requests/phut hien tai", rate, delta=threshold_status(rate, thr))
        st.caption(f"Tong request trong window: {len(requests)} | SLO: rate {thr['operator']} {thr['value']}/min")
        st.bar_chart(per_min.set_index("ts")["count"])
    else:
        st.info("Chua co request_received event.")

# ---- 3. Errors ----
with col3:
    p_errors = panel_by_id(config, "errors")
    st.subheader(f"{p_errors['title']} ({p_errors['unit']})")
    total_req = len(requests)
    total_fail = len(failures)
    error_rate = (total_fail / total_req * 100) if total_req else 0.0
    thr = p_errors["threshold"]
    st.metric("Error rate", f"{error_rate:.2f}%", delta=threshold_status(error_rate, thr))
    st.caption(f"SLO: error_rate {thr['operator']} {thr['value']}%")
    if not failures.empty and "error_type" in failures:
        st.bar_chart(failures["error_type"].value_counts())
    else:
        st.write("Khong co loi trong window nay.")

# ---- 4. Cost ----
with col4:
    p_cost = panel_by_id(config, "cost")
    st.subheader(f"{p_cost['title']} ({p_cost['unit']})")
    if not responses.empty:
        cost_total = responses["cost_usd"].astype(float).sum()
        thr = p_cost["threshold"]
        st.metric("Tong cost", f"${cost_total:.4f}", delta=threshold_status(cost_total, thr))
        st.caption(f"SLO: total {thr['operator']} ${thr['value']}")
        cost_per_min = responses.set_index("ts").resample("1min")["cost_usd"].sum().astype(float)
        st.line_chart(cost_per_min)
    else:
        st.info("Chua co response_sent event.")

# ---- 5. Tokens ----
with col5:
    p_tokens = panel_by_id(config, "tokens")
    st.subheader(f"{p_tokens['title']} ({p_tokens['unit']})")
    if not responses.empty:
        tin = responses["tokens_in"].astype(float).sum()
        tout = responses["tokens_out"].astype(float).sum()
        thr = p_tokens["threshold"]
        status = threshold_status(max(tin, tout), thr)
        st.metric("Tokens in / out", f"{int(tin)} / {int(tout)}", delta=status)
        st.caption(f"SLO: moi field {thr['operator']} {thr['value']} tokens")
        st.bar_chart(pd.DataFrame({"tokens": [tin, tout]}, index=["input", "output"]))
    else:
        st.info("Chua co response_sent event.")

# ---- 6. Quality ----
with col6:
    p_quality = panel_by_id(config, "quality")
    st.subheader(f"{p_quality['title']} ({p_quality['unit']})")
    if not responses.empty:
        mean_q = responses["quality_score"].astype(float).mean()
        thr = p_quality["threshold"]
        st.metric("Mean quality", f"{mean_q:.2f}", delta=threshold_status(mean_q, thr))
        st.caption(f"SLO: mean {thr['operator']} {thr['value']}")
        st.line_chart(responses.set_index("ts")["quality_score"].astype(float))
    else:
        st.info("Chua co response_sent event.")

st.divider()
st.caption("Nguon du lieu: data/logs.jsonl | Contract: config/dashboard.yaml")
