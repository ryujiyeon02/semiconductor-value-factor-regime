from __future__ import annotations

import json
import math
import os
import shutil
import unicodedata
from pathlib import Path

import nbformat as nbf
import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "input" / "processed_data"
ASSIGNMENT_MD = Path("과제_반도체_밸류팩터_국면검증.md (비공개 과제 명세)")
RAW_XLSX = Path("input/raw_data/4주차 데이터 원본.xlsx")
OUTPUT_DIR = BASE_DIR / "output" / "results"

START_PERF = pd.Timestamp("2007-01-31")
END_PERF = pd.Timestamp("2026-06-30")
SCALE = 1000.0


def nfc(text: object) -> str:
    return unicodedata.normalize("NFC", str(text)).strip()


def find_csv(nfc_name: str) -> Path:
    target = nfc(nfc_name)
    target_compact = target.replace(" ", "")
    for path in DATA_DIR.iterdir():
        if path.suffix.lower() == ".csv" and nfc(path.name) == target:
            return path
    for path in DATA_DIR.iterdir():
        if path.suffix.lower() == ".csv" and nfc(path.name).replace(" ", "") == target_compact:
            return path
    raise FileNotFoundError(f"CSV not found: {nfc_name}")


def parse_date_series(values: pd.Series) -> pd.Series:
    text = values.astype(str).str.strip().str.replace(".", "-", regex=False)
    return pd.to_datetime(text, errors="coerce")


def clean_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        out[col] = pd.to_numeric(
            out[col].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
    return out


def read_wide_csv(nfc_name: str) -> pd.DataFrame:
    path = find_csv(nfc_name)
    df = pd.read_csv(path)
    date_col = df.columns[0]
    df = df.rename(columns={date_col: "Date"})
    df["Date"] = parse_date_series(df["Date"])
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
    df.columns = [nfc(c) for c in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    return clean_numeric(df)


def read_optional_wide_csv(candidates: list[str]) -> tuple[pd.DataFrame | None, str | None]:
    for name in candidates:
        try:
            return read_wide_csv(name), name
        except FileNotFoundError:
            continue
    return None, None


def read_universe_csv(nfc_name: str) -> list[str]:
    path = find_csv(nfc_name)
    df = pd.read_csv(path)
    if df.empty:
        return []
    names = [nfc(x) for x in df.iloc[0, 1:].tolist()]
    return [x for x in names if x and x.lower() != "nan"]


def read_optional_universe_csv(candidates: list[str]) -> tuple[list[str] | None, str | None]:
    for name in candidates:
        try:
            return read_universe_csv(name), name
        except FileNotFoundError:
            continue
    return None, None


FINANCIAL_KEYWORDS = (
    "금융",
    "은행",
    "뱅크",
    "증권",
    "보험",
    "생명",
    "화재",
    "카드",
    "캐피탈",
    "리스",
    "신용",
    "저축",
    "종금",
    "종합금융",
    "스팩",
)
EXTRA_FINANCIAL_NAMES = {
    "현대해상",
    "코리안리",
    "SBI모기지",
    "SBI핀테크솔루션즈",
}


def make_financial_flag(columns: pd.Index) -> tuple[pd.Series, str, bool]:
    """Return a financial-sector flag.

    Prefer a DataGuide-extracted financial universe if the file exists. Without
    that mapping, fall back to stock-name keywords so the notebook remains
    runnable, but mark the source as approximate.
    """
    financial_names, source = read_optional_universe_csv(
        [
            "금융유니버스.csv",
            "금융 유니버스.csv",
            "금융업유니버스.csv",
            "금융업 유니버스.csv",
            "금융산업군.csv",
            "금융 산업군.csv",
            "은행보험증권유니버스.csv",
            "은행 보험 증권 유니버스.csv",
        ]
    )
    if financial_names is not None:
        financial_set = set(financial_names)
        flag = pd.Series({col: col in financial_set for col in columns}, dtype=bool)
        return flag, f"DataGuide 금융 유니버스 파일 `{source}`", True

    flag = pd.Series(
        {
            col: any(keyword in col for keyword in FINANCIAL_KEYWORDS)
            or col in EXTRA_FINANCIAL_NAMES
            for col in columns
        },
        dtype=bool,
    )
    return flag, "종목명 금융 키워드 fallback", False


def make_lagged_financial(financial: pd.DataFrame) -> pd.DataFrame:
    """Map unlagged fiscal-quarter values to the month when they are usable.

    The input has monthly rows, but each month in a fiscal quarter already carries
    that quarter's value. For the assignment rule, Q1-Q3 are made usable 3 months
    after quarter-end and Q4 is made usable 4 months after quarter-end.
    """
    tmp = financial.copy()
    tmp["__quarter"] = tmp.index.to_period("Q")
    quarterly = tmp.groupby("__quarter").last()

    availability_periods = []
    for q in quarterly.index:
        q_end_month = q.asfreq("M", how="end")
        lag_months = 4 if q.quarter == 4 else 3
        availability_periods.append(q_end_month + lag_months)

    quarterly.index = pd.PeriodIndex(availability_periods, freq="M")
    monthly_periods = pd.PeriodIndex(financial.index.to_period("M"), freq="M")
    all_periods = pd.period_range(monthly_periods.min(), monthly_periods.max(), freq="M")
    lagged_by_period = quarterly.reindex(all_periods).ffill()
    lagged = lagged_by_period.reindex(monthly_periods)
    lagged.index = financial.index
    return lagged


def make_lagged_ttm_financial(financial: pd.DataFrame) -> pd.DataFrame:
    """Build trailing-four-quarter values from quarterly data with reporting lags."""
    tmp = financial.copy()
    tmp["__quarter"] = tmp.index.to_period("Q")
    quarterly = tmp.groupby("__quarter").last()
    quarterly.index = pd.PeriodIndex(quarterly.index, freq="Q")

    ttm = quarterly.rolling(4).sum()
    availability_periods = []
    for q in ttm.index:
        q_end_month = q.asfreq("M", how="end")
        lag_months = 4 if q.quarter == 4 else 3
        availability_periods.append(q_end_month + lag_months)

    ttm.index = pd.PeriodIndex(availability_periods, freq="M")
    monthly_periods = pd.PeriodIndex(financial.index.to_period("M"), freq="M")
    all_periods = pd.period_range(monthly_periods.min(), monthly_periods.max(), freq="M")
    lagged_by_period = ttm.reindex(all_periods).ffill()
    lagged = lagged_by_period.reindex(monthly_periods)
    lagged.index = financial.index
    return lagged


def make_lagged_fiscal_year_sum(financial: pd.DataFrame) -> pd.DataFrame:
    """Build a fiscal-year value from quarterly data and apply the Q4 reporting lag."""
    tmp = financial.copy()
    tmp["__quarter"] = tmp.index.to_period("Q")
    quarterly = tmp.groupby("__quarter").last()
    quarterly.index = pd.PeriodIndex(quarterly.index, freq="Q")

    fiscal_year = quarterly.rolling(4).sum()
    fiscal_year = fiscal_year.loc[fiscal_year.index.quarter == 4]
    availability_periods = [q.asfreq("M", how="end") + 4 for q in fiscal_year.index]

    fiscal_year.index = pd.PeriodIndex(availability_periods, freq="M")
    monthly_periods = pd.PeriodIndex(financial.index.to_period("M"), freq="M")
    all_periods = pd.period_range(monthly_periods.min(), monthly_periods.max(), freq="M")
    lagged_by_period = fiscal_year.reindex(all_periods).ffill()
    lagged = lagged_by_period.reindex(monthly_periods)
    lagged.index = financial.index
    return lagged


def winsorize_row(row: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    valid = row.dropna()
    if len(valid) < 5:
        return row * np.nan
    lo = valid.quantile(lower)
    hi = valid.quantile(upper)
    return row.clip(lo, hi)


def zscore_cross_section(factor: pd.DataFrame) -> pd.DataFrame:
    clipped = factor.apply(winsorize_row, axis=1)
    mean = clipped.mean(axis=1)
    std = clipped.std(axis=1).replace(0, np.nan)
    return clipped.sub(mean, axis=0).div(std, axis=0)


def compute_ic(factor: pd.DataFrame, forward_returns: pd.DataFrame, min_n: int = 10) -> pd.Series:
    values = {}
    for date in factor.index:
        if date not in forward_returns.index:
            continue
        f = factor.loc[date]
        r = forward_returns.loc[date]
        valid = f.notna() & r.notna()
        if valid.sum() < min_n:
            values[date] = np.nan
        else:
            values[date] = f[valid].rank().corr(r[valid].rank())
    return pd.Series(values, name="IC").sort_index()


def quintile_returns(
    factor: pd.DataFrame, forward_returns: pd.DataFrame, min_n: int = 25
) -> pd.DataFrame:
    records = []
    for date in factor.index:
        if date not in forward_returns.index:
            continue
        f = factor.loc[date]
        r = forward_returns.loc[date]
        valid = f.notna() & r.notna()
        if valid.sum() < min_n:
            continue
        ranks = f[valid].rank(method="first")
        try:
            buckets = pd.qcut(ranks, 5, labels=[1, 2, 3, 4, 5])
        except ValueError:
            continue
        row = {"Date": date}
        for q in [1, 2, 3, 4, 5]:
            names = buckets[buckets == q].index
            row[f"Q{q}"] = r[names].mean()
        records.append(row)
    if not records:
        return pd.DataFrame(columns=["Q1", "Q2", "Q3", "Q4", "Q5"])
    return pd.DataFrame(records).set_index("Date").sort_index()


def nav_from_returns(returns: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    return (1 + returns.dropna()).cumprod()


def max_drawdown(nav: pd.Series) -> float:
    dd = nav / nav.cummax() - 1
    return float(dd.min())


def performance_metrics(returns: pd.Series) -> dict[str, float]:
    ret = returns.dropna()
    if ret.empty:
        return {"개월수": 0, "NAV": np.nan, "CAGR": np.nan, "Ann.Vol": np.nan, "Sharpe": np.nan, "MDD": np.nan}
    nav = nav_from_returns(ret)
    years = len(ret) / 12.0
    cagr = float(nav.iloc[-1] ** (1 / years) - 1) if years > 0 and nav.iloc[-1] > 0 else np.nan
    vol = float(ret.std(ddof=1) * math.sqrt(12)) if len(ret) > 1 else np.nan
    sharpe = float(ret.mean() / ret.std(ddof=1) * math.sqrt(12)) if len(ret) > 1 and ret.std(ddof=1) != 0 else np.nan
    return {
        "개월수": int(len(ret)),
        "NAV": float(nav.iloc[-1]),
        "CAGR": cagr,
        "Ann.Vol": vol,
        "Sharpe": sharpe,
        "MDD": max_drawdown(nav),
    }


def top_n_returns(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    market_cap: pd.DataFrame,
    top_mcap: int = 300,
    top_n: int = 20,
    universe_cols: list[str] | pd.Index | None = None,
    candidate_cols: list[str] | pd.Index | None = None,
    share_reference: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series, dict[pd.Timestamp, list[str]]]:
    returns = {}
    reference_share = {}
    selected_names = {}
    base_allowed = pd.Index(universe_cols) if universe_cols is not None else pd.Index(factor.columns)
    candidate_allowed = pd.Index(candidate_cols) if candidate_cols is not None else None
    for date in factor.index:
        if date not in forward_returns.index or date not in market_cap.index:
            continue
        mcap_slice = market_cap.loc[date, base_allowed.intersection(market_cap.columns)].dropna()
        base = mcap_slice.sort_values(ascending=False).head(top_mcap).index
        candidates_index = base
        if candidate_allowed is not None:
            candidates_index = candidates_index.intersection(candidate_allowed)
        f = factor.loc[date, candidates_index.intersection(factor.columns)]
        r = forward_returns.loc[date, f.index]
        valid = f.notna() & r.notna()
        candidates = f[valid]
        if len(candidates) < top_n:
            continue
        selected = candidates.sort_values(ascending=False).head(top_n).index.tolist()
        returns[date] = forward_returns.loc[date, selected].mean()
        if share_reference is not None:
            selected_flag = share_reference.reindex(selected).fillna(False)
            reference_share[date] = float(selected_flag.mean())
        else:
            reference_share[date] = np.nan
        selected_names[date] = selected
    return (
        pd.Series(returns, name="return").sort_index(),
        pd.Series(reference_share, name="reference_share").sort_index(),
        selected_names,
    )


def pct(x: float) -> str:
    if pd.isna(x):
        return ""
    return f"{x:.2%}"


def num(x: float) -> str:
    if pd.isna(x):
        return ""
    return f"{x:.3f}"


def format_table(df: pd.DataFrame, percent_cols: set[str] | None = None) -> str:
    percent_cols = percent_cols or set()
    out = df.copy()
    for col in out.columns:
        if col in percent_cols:
            out[col] = out[col].map(pct)
        elif pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].map(num)
    return out.to_markdown(index=False)


def validation_stat_row(
    check: str, left: pd.DataFrame, right: pd.DataFrame, valid_mask: pd.DataFrame
) -> dict[str, float | int | str]:
    aligned = pd.concat(
        [
            left.where(valid_mask).stack().rename("left"),
            right.where(valid_mask).stack().rename("right"),
        ],
        axis=1,
    ).dropna()
    diff = (aligned["left"] - aligned["right"]).abs()
    return {
        "Check": check,
        "Median Abs Diff": diff.median(),
        "Mean Abs Diff": diff.mean(),
        "Correlation": aligned["left"].corr(aligned["right"]),
        "Coverage Obs": int(len(aligned)),
    }


def save_figures(
    quintile_navs: dict[str, pd.DataFrame],
    ic: pd.DataFrame,
    ism: pd.Series,
    universe_share: pd.DataFrame,
    universe_nav: pd.DataFrame,
    validation_series: pd.DataFrame,
    validation_name: str,
) -> dict[str, Path]:
    plt.rcParams["font.family"] = ["AppleGothic", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    paths: dict[str, Path] = {}

    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for ax, (factor_name, nav) in zip(axes, quintile_navs.items()):
        for col in nav.columns:
            ax.plot(nav.index, nav[col], linewidth=1.0, label=col)
        ax.set_title(f"{factor_name} quintile NAV, cyclical universe")
        ax.set_ylabel("NAV")
        ax.grid(alpha=0.25)
        ax.legend(ncol=5, fontsize=8)
    fig.tight_layout()
    path = OUTPUT_DIR / "quintile_nav_ep_bp.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["quintile_nav"] = path

    rolling_ic = ic.rolling(12, min_periods=6).mean()
    ep_col = next((col for col in rolling_ic.columns if col.startswith("E/P")), "E/P")
    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax1.plot(rolling_ic.index, rolling_ic[ep_col], label=f"{ep_col} rolling IC", color="#377eb8")
    ax1.plot(rolling_ic.index, rolling_ic["B/P"], label="B/P rolling IC", color="#e41a1c")
    ax1.axhline(0, color="black", linewidth=0.8)
    ax1.set_ylabel("12M rolling IC")
    ax1.grid(alpha=0.25)
    ax2 = ax1.twinx()
    ax2.plot(ism.index, ism, label="ISM PMI", color="#4daf4a", alpha=0.45)
    ax2.set_ylabel("ISM PMI")
    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, loc="upper left", fontsize=8)
    ax1.set_title("Rolling IC with ISM PMI overlay")
    fig.tight_layout()
    path = OUTPUT_DIR / "rolling_ic_pmi.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["rolling_ic"] = path

    fig, ax = plt.subplots(figsize=(11, 4))
    for col in universe_share.columns:
        ax.plot(universe_share.index, universe_share[col], label=col, linewidth=1.0)
    ax.set_title("Financial weight in full-market top-20 value basket")
    ax.set_ylabel("Financial weight")
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = OUTPUT_DIR / "full_market_financial_share_top20.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["universe_share"] = path

    fig, ax = plt.subplots(figsize=(11, 5))
    for col in universe_nav.columns:
        ax.plot(universe_nav.index, universe_nav[col], label=col, linewidth=1.0)
    ax.set_title("Full market vs financial-excluded universe, top-20 value basket NAV")
    ax.set_ylabel("NAV")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = OUTPUT_DIR / "full_vs_ex_financial_nav.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["universe_nav"] = path

    fig, ax = plt.subplots(figsize=(10, 4.8))
    for col in validation_series.columns:
        ax.plot(validation_series.index, validation_series[col], label=col, linewidth=1.2)
    ax.axvspan(pd.Timestamp("2024-01-31"), pd.Timestamp("2024-03-31"), color="#fdae61", alpha=0.18)
    ax.set_title(f"{validation_name} E/P validation with fiscal-year PER")
    ax.set_ylabel("E/P")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = OUTPUT_DIR / "ep_validation_per.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["validation"] = path

    return paths


def build_notebook(notebook_path: Path) -> None:
    nb = nbf.v4.new_notebook()
    cells = [
        nbf.v4.new_markdown_cell(
            "# 반도체 밸류 팩터 국면 검증 재현 노트북\n\n"
            "이 노트북은 4주차 과제 Markdown의 요구사항에 맞춰 원천 이익·자본·시가총액으로 "
            "E/P와 B/P를 직접 계산하고, lag 적용 후 IC·분위 포트폴리오·금융 포함/제외 실험을 재현합니다."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "import math, unicodedata\n"
            "import numpy as np\n"
            "import pandas as pd\n"
            "import matplotlib.pyplot as plt\n\n"
            "BASE_DIR = Path.cwd().parent if Path.cwd().name == 'notebooks' else Path.cwd()\n"
            "DATA_DIR = BASE_DIR / 'input' / 'processed_data'\n"
            "OUTPUT_DIR = BASE_DIR / 'output' / 'results'\n"
            "SCALE = 1000.0\n"
            "START_PERF = pd.Timestamp('2007-01-31')\n"
            "END_PERF = pd.Timestamp('2026-06-30')"
        ),
        nbf.v4.new_markdown_cell("## 0. 데이터 로더"),
        nbf.v4.new_code_cell(
            "def nfc(text):\n"
            "    return unicodedata.normalize('NFC', str(text)).strip()\n\n"
            "def find_csv(name):\n"
            "    target = nfc(name)\n"
            "    for path in DATA_DIR.iterdir():\n"
            "        if path.suffix.lower() == '.csv' and nfc(path.name) == target:\n"
            "            return path\n"
            "    raise FileNotFoundError(name)\n\n"
            "def read_wide_csv(name):\n"
            "    df = pd.read_csv(find_csv(name))\n"
            "    df = df.rename(columns={df.columns[0]: 'Date'})\n"
            "    df['Date'] = pd.to_datetime(df['Date'].astype(str).str.replace('.', '-', regex=False), errors='coerce')\n"
            "    df = df.dropna(subset=['Date']).set_index('Date').sort_index()\n"
            "    df.columns = [nfc(c) for c in df.columns]\n"
            "    df = df.loc[:, ~pd.Index(df.columns).duplicated()]\n"
            "    for col in df.columns:\n"
            "        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '', regex=False), errors='coerce')\n"
            "    return df\n\n"
            "def read_universe_csv(name):\n"
            "    df = pd.read_csv(find_csv(name))\n"
            "    return [nfc(x) for x in df.iloc[0, 1:].tolist() if nfc(x) and nfc(x).lower() != 'nan']\n\n"
            "def read_optional_universe_csv(candidates):\n"
            "    for name in candidates:\n"
            "        try:\n"
            "            return read_universe_csv(name), name\n"
            "        except FileNotFoundError:\n"
            "            continue\n"
            "    return None, None\n\n"
            "FINANCIAL_KEYWORDS = ('금융','은행','뱅크','증권','보험','생명','화재','카드','캐피탈','리스','신용','저축','종금','종합금융','스팩')\n"
            "EXTRA_FINANCIAL_NAMES = {'현대해상','코리안리','SBI모기지','SBI핀테크솔루션즈'}\n\n"
            "def make_financial_flag(columns):\n"
            "    names, source = read_optional_universe_csv(['금융유니버스.csv','금융 유니버스.csv','금융업유니버스.csv','금융업 유니버스.csv','금융산업군.csv','금융 산업군.csv','은행보험증권유니버스.csv','은행 보험 증권 유니버스.csv'])\n"
            "    if names is not None:\n"
            "        financial_set = set(names)\n"
            "        return pd.Series({c: c in financial_set for c in columns}, dtype=bool), f'DataGuide 금융 유니버스 파일 {source}', True\n"
            "    flag = pd.Series({c: any(k in c for k in FINANCIAL_KEYWORDS) or c in EXTRA_FINANCIAL_NAMES for c in columns}, dtype=bool)\n"
            "    return flag, '종목명 금융 키워드 fallback', False"
        ),
        nbf.v4.new_markdown_cell("## 1. 원천 데이터 로드 및 lag 적용"),
        nbf.v4.new_code_cell(
            "price = read_wide_csv('수정주가.csv')\n"
            "mcap = read_wide_csv('시가총액.csv')\n"
            "income = read_wide_csv('당기순이익(지배),분기.csv')\n"
            "equity = read_wide_csv('자본총계(지배),분기.csv')\n"
            "per = read_wide_csv('PER(IFRS-연결).csv')\n"
            "pe = read_wide_csv('P:E.csv')\n"
            "ism = read_wide_csv('미국 ISM.csv').iloc[:, 0]\n"
            "cyclical_names = read_universe_csv('시클리컬 유니버스.csv')\n\n"
            "manufacturing_names = read_universe_csv('제조업유니버스.csv')\n\n"
            "common_dates = price.index.intersection(mcap.index).intersection(income.index).intersection(equity.index)\n"
            "common_cols = price.columns.intersection(mcap.columns).intersection(income.columns).intersection(equity.columns)\n"
            "price, mcap, income, equity = [x.loc[common_dates, common_cols] for x in [price, mcap, income, equity]]\n"
            "pe = pe.reindex(index=common_dates, columns=common_cols)\n\n"
            "per = per.reindex(index=common_dates, columns=common_cols)\n\n"
            "def make_lagged_financial(financial):\n"
            "    tmp = financial.copy()\n"
            "    tmp['__quarter'] = tmp.index.to_period('Q')\n"
            "    quarterly = tmp.groupby('__quarter').last()\n"
            "    availability = []\n"
            "    for q in quarterly.index:\n"
            "        q_end_month = q.asfreq('M', how='end')\n"
            "        availability.append(q_end_month + (4 if q.quarter == 4 else 3))\n"
            "    quarterly.index = pd.PeriodIndex(availability, freq='M')\n"
            "    months = pd.PeriodIndex(financial.index.to_period('M'), freq='M')\n"
            "    all_months = pd.period_range(months.min(), months.max(), freq='M')\n"
            "    lagged = quarterly.reindex(all_months).ffill().reindex(months)\n"
            "    lagged.index = financial.index\n"
            "    return lagged\n\n"
            "def make_lagged_ttm_financial(financial):\n"
            "    tmp = financial.copy()\n"
            "    tmp['__quarter'] = tmp.index.to_period('Q')\n"
            "    quarterly = tmp.groupby('__quarter').last()\n"
            "    quarterly.index = pd.PeriodIndex(quarterly.index, freq='Q')\n"
            "    ttm = quarterly.rolling(4).sum()\n"
            "    availability = []\n"
            "    for q in ttm.index:\n"
            "        q_end_month = q.asfreq('M', how='end')\n"
            "        availability.append(q_end_month + (4 if q.quarter == 4 else 3))\n"
            "    ttm.index = pd.PeriodIndex(availability, freq='M')\n"
            "    months = pd.PeriodIndex(financial.index.to_period('M'), freq='M')\n"
            "    all_months = pd.period_range(months.min(), months.max(), freq='M')\n"
            "    lagged = ttm.reindex(all_months).ffill().reindex(months)\n"
            "    lagged.index = financial.index\n"
            "    return lagged\n\n"
            "def make_lagged_fiscal_year_sum(financial):\n"
            "    tmp = financial.copy()\n"
            "    tmp['__quarter'] = tmp.index.to_period('Q')\n"
            "    quarterly = tmp.groupby('__quarter').last()\n"
            "    quarterly.index = pd.PeriodIndex(quarterly.index, freq='Q')\n"
            "    fiscal_year = quarterly.rolling(4).sum().loc[lambda x: x.index.quarter == 4]\n"
            "    fiscal_year.index = pd.PeriodIndex([q.asfreq('M', how='end') + 4 for q in fiscal_year.index], freq='M')\n"
            "    months = pd.PeriodIndex(financial.index.to_period('M'), freq='M')\n"
            "    all_months = pd.period_range(months.min(), months.max(), freq='M')\n"
            "    lagged = fiscal_year.reindex(all_months).ffill().reindex(months)\n"
            "    lagged.index = financial.index\n"
            "    return lagged\n\n"
            "lag_income = make_lagged_financial(income)\n"
            "lag_equity = make_lagged_financial(equity)\n"
            "ttm_income = make_lagged_ttm_financial(income)\n"
            "fiscal_income = make_lagged_fiscal_year_sum(income)\n"
            "ep_quarterly = lag_income / (mcap * SCALE)\n"
            "ep_unlagged = income / (mcap * SCALE)\n"
            "ep_ttm = ttm_income / (mcap * SCALE)\n"
            "ep_fiscal_year = fiscal_income / (mcap * SCALE)\n"
            "ep = ep_quarterly\n"
            "bp = lag_equity / (mcap * SCALE)\n"
            "per_inverse = 1 / per.replace(0, np.nan)\n"
            "pe_inverse = 1 / pe.replace(0, np.nan)\n"
            "forward_returns = price.pct_change(fill_method=None).shift(-1)\n"
            "cyc_cols = [x for x in cyclical_names if x in common_cols]\n"
            "mfg_cols = [x for x in manufacturing_names if x in common_cols]\n"
            "financial_flag, financial_source, financial_is_exact = make_financial_flag(common_cols)\n"
            "non_fin_cols = [x for x in common_cols if not financial_flag[x]]\n"
            "len(common_cols), len(cyc_cols), len(mfg_cols), int(financial_flag.sum()), financial_source, common_dates.min(), common_dates.max()"
        ),
        nbf.v4.new_markdown_cell("## 2. 표준화, IC, 분위 포트폴리오"),
        nbf.v4.new_code_cell(
            "def winsorize_row(row, lower=0.01, upper=0.99):\n"
            "    valid = row.dropna()\n"
            "    if len(valid) < 5:\n"
            "        return row * np.nan\n"
            "    return row.clip(valid.quantile(lower), valid.quantile(upper))\n\n"
            "def zscore_cross_section(factor):\n"
            "    clipped = factor.apply(winsorize_row, axis=1)\n"
            "    return clipped.sub(clipped.mean(axis=1), axis=0).div(clipped.std(axis=1).replace(0, np.nan), axis=0)\n\n"
            "def compute_ic(factor, returns, min_n=10):\n"
            "    out = {}\n"
            "    for date in factor.index:\n"
            "        f, r = factor.loc[date], returns.loc[date]\n"
            "        valid = f.notna() & r.notna()\n"
            "        out[date] = f[valid].rank().corr(r[valid].rank()) if valid.sum() >= min_n else np.nan\n"
            "    return pd.Series(out)\n\n"
            "def quintile_returns(factor, returns, min_n=25):\n"
            "    rows = []\n"
            "    for date in factor.index:\n"
            "        f, r = factor.loc[date], returns.loc[date]\n"
            "        valid = f.notna() & r.notna()\n"
            "        if valid.sum() < min_n:\n"
            "            continue\n"
            "        buckets = pd.qcut(f[valid].rank(method='first'), 5, labels=[1,2,3,4,5])\n"
            "        row = {'Date': date}\n"
            "        for q in [1,2,3,4,5]:\n"
            "            row[f'Q{q}'] = r[buckets[buckets == q].index].mean()\n"
            "        rows.append(row)\n"
            "    return pd.DataFrame(rows).set_index('Date')\n\n"
            "ep_z = zscore_cross_section(ep[cyc_cols])\n"
            "bp_z = zscore_cross_section(bp[cyc_cols])\n"
            "ret_cyc = forward_returns[cyc_cols]\n"
            "mask = (ep_z.index >= START_PERF) & (ep_z.index <= END_PERF)\n"
            "ic = pd.DataFrame({'E/P': compute_ic(ep_z, ret_cyc), 'B/P': compute_ic(bp_z, ret_cyc)}).loc[mask]\n"
            "ep_q = quintile_returns(ep_z.loc[mask], ret_cyc.loc[mask])\n"
            "bp_q = quintile_returns(bp_z.loc[mask], ret_cyc.loc[mask])\n"
            "ic.mean(), ep_q.mean(), bp_q.mean()"
        ),
        nbf.v4.new_markdown_cell("## 3. 성과 지표"),
        nbf.v4.new_code_cell(
            "def perf_metrics(ret):\n"
            "    ret = ret.dropna()\n"
            "    nav = (1 + ret).cumprod()\n"
            "    years = len(ret) / 12\n"
            "    return pd.Series({\n"
            "        'months': len(ret),\n"
            "        'NAV': nav.iloc[-1],\n"
            "        'CAGR': nav.iloc[-1] ** (1 / years) - 1,\n"
            "        'Ann.Vol': ret.std() * np.sqrt(12),\n"
            "        'Sharpe': ret.mean() / ret.std() * np.sqrt(12),\n"
            "        'MDD': (nav / nav.cummax() - 1).min(),\n"
            "    })\n\n"
            "summary = pd.DataFrame({\n"
            "    'E/P Q5': perf_metrics(ep_q['Q5']),\n"
            "    'B/P Q5': perf_metrics(bp_q['Q5']),\n"
            "}).T\n"
            "summary"
        ),
        nbf.v4.new_markdown_cell("## 3-a. E/P 정의 비교"),
        nbf.v4.new_code_cell(
            "def ep_definition_eval(label, factor):\n"
            "    factor_z = zscore_cross_section(factor[cyc_cols])\n"
            "    factor_ic = compute_ic(factor_z, ret_cyc).loc[mask]\n"
            "    factor_q = quintile_returns(factor_z.loc[mask], ret_cyc.loc[mask])\n"
            "    q5 = perf_metrics(factor_q['Q5'])\n"
            "    return pd.Series({\n"
            "        'E/P Definition': label,\n"
            "        'Mean IC': factor_ic.mean(),\n"
            "        'IC IR': factor_ic.mean() / factor_ic.std(),\n"
            "        'Positive IC Ratio': (factor_ic > 0).mean(),\n"
            "        'Q5 CAGR': q5['CAGR'],\n"
            "        'Q5 Sharpe': q5['Sharpe'],\n"
            "        'Q5 MDD': q5['MDD'],\n"
            "    })\n\n"
            "pd.DataFrame([\n"
            "    ep_definition_eval('Quarterly lagged E/P (strategy)', ep_quarterly),\n"
            "    ep_definition_eval('TTM lagged E/P', ep_ttm),\n"
            "    ep_definition_eval('Fiscal-year lagged E/P', ep_fiscal_year),\n"
            "])"
        ),
        nbf.v4.new_markdown_cell("## 3-b. PER 검증"),
        nbf.v4.new_code_cell(
            "per_cols = per_inverse.notna().sum()[lambda s: s > 0].index.tolist()\n"
            "validation_name = per_cols[0]\n"
            "valid_per = per_inverse[validation_name].notna() & ep_fiscal_year[validation_name].notna()\n"
            "valid_per_ttm = per_inverse[validation_name].notna() & ep_ttm[validation_name].notna()\n"
            "valid_pe = pe_inverse[validation_name].notna() & ep_unlagged[validation_name].notna()\n"
            "pd.DataFrame({\n"
            "    'Check': ['Fiscal-year lagged E/P vs 1/PER', 'TTM lagged E/P vs 1/PER', 'Quarterly unlagged E/P vs 1/P:E', 'Quarterly lagged E/P vs 1/P:E'],\n"
            "    'Median Abs Diff': [\n"
            "        (ep_fiscal_year.loc[valid_per, validation_name] - per_inverse.loc[valid_per, validation_name]).abs().median(),\n"
            "        (ep_ttm.loc[valid_per_ttm, validation_name] - per_inverse.loc[valid_per_ttm, validation_name]).abs().median(),\n"
            "        (ep_unlagged.loc[valid_pe, validation_name] - pe_inverse.loc[valid_pe, validation_name]).abs().median(),\n"
            "        (ep_quarterly.loc[valid_pe, validation_name] - pe_inverse.loc[valid_pe, validation_name]).abs().median(),\n"
            "    ],\n"
            "    'Correlation': [\n"
            "        ep_fiscal_year.loc[valid_per, validation_name].corr(per_inverse.loc[valid_per, validation_name]),\n"
            "        ep_ttm.loc[valid_per_ttm, validation_name].corr(per_inverse.loc[valid_per_ttm, validation_name]),\n"
            "        ep_unlagged.loc[valid_pe, validation_name].corr(pe_inverse.loc[valid_pe, validation_name]),\n"
            "        ep_quarterly.loc[valid_pe, validation_name].corr(pe_inverse.loc[valid_pe, validation_name]),\n"
            "    ],\n"
            "    'Coverage Obs': [valid_per.sum(), valid_per_ttm.sum(), valid_pe.sum(), valid_pe.sum()],\n"
            "})"
        ),
        nbf.v4.new_markdown_cell("## 4. 금융 포함/제외 대조 실험"),
        nbf.v4.new_code_cell(
            "# A: 전체 시장, B: 금융 제외. 제조업 유니버스는 부정확한 근사로 참고만 확인.\n"
            "manufacturing_flag = pd.Series({c: c in set(mfg_cols) for c in common_cols})\n"
            "financial_flag = financial_flag.reindex(common_cols).fillna(False)\n"
            "non_fin_cols = [c for c in common_cols if not financial_flag[c]]\n\n"
            "def top_n_returns(factor, returns, mcap, universe_cols=None, candidate_cols=None, share_reference=None, top_mcap=300, top_n=20):\n"
            "    out, ref_share = {}, {}\n"
            "    base_allowed = pd.Index(universe_cols) if universe_cols is not None else pd.Index(factor.columns)\n"
            "    candidate_allowed = pd.Index(candidate_cols) if candidate_cols is not None else None\n"
            "    for date in factor.index:\n"
            "        base = mcap.loc[date, base_allowed.intersection(mcap.columns)].dropna().sort_values(ascending=False).head(top_mcap).index\n"
            "        candidates_index = base if candidate_allowed is None else base.intersection(candidate_allowed)\n"
            "        f = factor.loc[date, candidates_index.intersection(factor.columns)]\n"
            "        r = returns.loc[date, f.index]\n"
            "        valid = f.notna() & r.notna()\n"
            "        cand = f[valid]\n"
            "        if len(cand) < top_n:\n"
            "            continue\n"
            "        selected = cand.sort_values(ascending=False).head(top_n).index\n"
            "        out[date] = returns.loc[date, selected].mean()\n"
            "        ref_share[date] = share_reference.reindex(selected).fillna(False).mean() if share_reference is not None else np.nan\n"
            "    return pd.Series(out), pd.Series(ref_share)\n\n"
            "ep_a, ep_a_fin_share = top_n_returns(ep, forward_returns, mcap, share_reference=financial_flag)\n"
            "ep_b, _ = top_n_returns(ep, forward_returns, mcap, candidate_cols=non_fin_cols)\n"
            "bp_a, bp_a_fin_share = top_n_returns(bp, forward_returns, mcap, share_reference=financial_flag)\n"
            "bp_b, _ = top_n_returns(bp, forward_returns, mcap, candidate_cols=non_fin_cols)\n"
            "ep_mfg, _ = top_n_returns(ep, forward_returns, mcap, candidate_cols=mfg_cols)\n"
            "bp_mfg, _ = top_n_returns(bp, forward_returns, mcap, candidate_cols=mfg_cols)\n"
            "pd.DataFrame({'E/P A 전체': perf_metrics(ep_a.loc[START_PERF:END_PERF]), 'E/P B 금융 제외': perf_metrics(ep_b.loc[START_PERF:END_PERF]), 'B/P A 전체': perf_metrics(bp_a.loc[START_PERF:END_PERF]), 'B/P B 금융 제외': perf_metrics(bp_b.loc[START_PERF:END_PERF]), 'E/P 제조업 참고': perf_metrics(ep_mfg.loc[START_PERF:END_PERF]), 'B/P 제조업 참고': perf_metrics(bp_mfg.loc[START_PERF:END_PERF])}).T"
        ),
        nbf.v4.new_markdown_cell("## 5. 그래프"),
        nbf.v4.new_code_cell(
            "rolling_ic = ic.rolling(12, min_periods=6).mean()\n"
            "ism_plot = ism.loc[rolling_ic.index.min():rolling_ic.index.max()].dropna()\n\n"
            "fig, ax1 = plt.subplots(figsize=(11, 5))\n"
            "ep_col = next(col for col in rolling_ic.columns if col.startswith('E/P'))\n"
            "ax1.plot(rolling_ic.index, rolling_ic[ep_col], label=f'{ep_col} rolling IC', color='tab:blue')\n"
            "ax1.plot(rolling_ic.index, rolling_ic['B/P'], label='B/P rolling IC', color='tab:red')\n"
            "ax1.axhline(0, color='black', linewidth=0.8)\n"
            "ax1.set_ylabel('12M rolling IC')\n"
            "ax1.grid(alpha=0.25)\n\n"
            "ax2 = ax1.twinx()\n"
            "ax2.plot(ism_plot.index, ism_plot.values, label='ISM PMI', color='tab:green', alpha=0.45)\n"
            "ax2.set_ylabel('ISM PMI')\n\n"
            "lines1, labels1 = ax1.get_legend_handles_labels()\n"
            "lines2, labels2 = ax2.get_legend_handles_labels()\n"
            "ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')\n"
            "ax1.set_title('12M rolling IC with ISM PMI overlay')\n"
            "fig.tight_layout()\n"
            "plt.show()"
        ),
    ]
    nb["cells"] = cells
    nb["metadata"] = {
        "kernelspec": {"display_name": "Python (pydata)", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "pygments_lexer": "ipython3"},
    }
    notebook_path.write_text(nbf.writes(nb), encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    price = read_wide_csv("수정주가.csv")
    mcap = read_wide_csv("시가총액.csv")
    income = read_wide_csv("당기순이익(지배),분기.csv")
    equity = read_wide_csv("자본총계(지배),분기.csv")
    pe = read_wide_csv("P:E.csv")
    per, per_source = read_optional_wide_csv(["PER(IFRS-연결).csv", "PER.csv"])
    ism_df = read_wide_csv("미국 ISM.csv")
    ism = ism_df.iloc[:, 0].rename("ISM PMI")
    cyclical_names = read_universe_csv("시클리컬 유니버스.csv")
    manufacturing_names = read_universe_csv("제조업유니버스.csv")

    common_dates = (
        price.index.intersection(mcap.index)
        .intersection(income.index)
        .intersection(equity.index)
    )
    common_cols = (
        price.columns.intersection(mcap.columns)
        .intersection(income.columns)
        .intersection(equity.columns)
    )
    price = price.loc[common_dates, common_cols]
    mcap = mcap.loc[common_dates, common_cols]
    income = income.loc[common_dates, common_cols]
    equity = equity.loc[common_dates, common_cols]
    pe = pe.reindex(index=common_dates, columns=common_cols)
    if per is not None:
        per = per.reindex(index=common_dates, columns=common_cols)

    lag_income = make_lagged_financial(income)
    lag_equity = make_lagged_financial(equity)
    ttm_income = make_lagged_ttm_financial(income)
    fiscal_income = make_lagged_fiscal_year_sum(income)

    ep_unlagged = income / (mcap * SCALE)
    ep_quarterly = lag_income / (mcap * SCALE)
    ep_ttm = ttm_income / (mcap * SCALE)
    ep = ep_quarterly
    ep_fiscal_year = fiscal_income / (mcap * SCALE)
    bp = lag_equity / (mcap * SCALE)
    pe_inverse = 1 / pe.replace(0, np.nan)
    per_inverse = 1 / per.replace(0, np.nan) if per is not None else None
    forward_returns = price.pct_change(fill_method=None).shift(-1)

    cyc_cols = [name for name in cyclical_names if name in common_cols]
    missing_cyc = [name for name in cyclical_names if name not in common_cols]
    mfg_cols = [name for name in manufacturing_names if name in common_cols]
    missing_mfg = [name for name in manufacturing_names if name not in common_cols]
    financial_flag, financial_source_text, financial_is_exact = make_financial_flag(common_cols)
    financial_flag = financial_flag.reindex(common_cols).fillna(False)
    non_fin_cols = [name for name in common_cols if not financial_flag[name]]

    date_mask = (price.index >= START_PERF) & (price.index <= END_PERF)
    ep_z_cyc = zscore_cross_section(ep.loc[:, cyc_cols])
    bp_z_cyc = zscore_cross_section(bp.loc[:, cyc_cols])
    ret_cyc = forward_returns.loc[:, cyc_cols]

    ic = pd.DataFrame(
        {
            "E/P": compute_ic(ep_z_cyc, ret_cyc),
            "B/P": compute_ic(bp_z_cyc, ret_cyc),
        }
    ).loc[date_mask]

    ep_quint = quintile_returns(ep_z_cyc.loc[date_mask], ret_cyc.loc[date_mask])
    bp_quint = quintile_returns(bp_z_cyc.loc[date_mask], ret_cyc.loc[date_mask])
    ep_quint_nav = nav_from_returns(ep_quint)
    bp_quint_nav = nav_from_returns(bp_quint)

    perf_rows = []
    for label, ret in {
        "Cyclical E/P top quintile": ep_quint["Q5"],
        "Cyclical B/P top quintile": bp_quint["Q5"],
        "Cyclical E/P top-bottom": ep_quint["Q5"] - ep_quint["Q1"],
        "Cyclical B/P top-bottom": bp_quint["Q5"] - bp_quint["Q1"],
    }.items():
        row = {"Portfolio": label}
        row.update(performance_metrics(ret))
        perf_rows.append(row)
    perf_summary = pd.DataFrame(perf_rows)

    ic_summary = pd.DataFrame(
        [
            {
                "Factor": col,
                "Mean IC": ic[col].mean(),
                "IC Std": ic[col].std(ddof=1),
                "IC IR": ic[col].mean() / ic[col].std(ddof=1),
                "Positive IC Ratio": (ic[col] > 0).mean(),
            }
            for col in ic.columns
        ]
    )

    ep_definition_rows = []
    for label, factor in {
        "Quarterly lagged E/P (strategy)": ep_quarterly,
        "TTM lagged E/P": ep_ttm,
        "Fiscal-year lagged E/P": ep_fiscal_year,
    }.items():
        factor_z = zscore_cross_section(factor.loc[:, cyc_cols])
        factor_ic = compute_ic(factor_z, ret_cyc).loc[date_mask]
        factor_quint = quintile_returns(factor_z.loc[date_mask], ret_cyc.loc[date_mask])
        q5_metrics = performance_metrics(factor_quint["Q5"]) if "Q5" in factor_quint else {}
        ep_definition_rows.append(
            {
                "E/P Definition": label,
                "Mean IC": factor_ic.mean(),
                "IC Std": factor_ic.std(ddof=1),
                "IC IR": factor_ic.mean() / factor_ic.std(ddof=1),
                "Positive IC Ratio": (factor_ic > 0).mean(),
                "Q5 CAGR": q5_metrics.get("CAGR", np.nan),
                "Q5 Sharpe": q5_metrics.get("Sharpe", np.nan),
                "Q5 MDD": q5_metrics.get("MDD", np.nan),
                "Q5 Months": q5_metrics.get("개월수", 0),
            }
        )
    ep_definition_summary = pd.DataFrame(ep_definition_rows)

    recent_mask = (price.index >= pd.Timestamp("2023-07-31")) & (
        price.index <= pd.Timestamp("2026-06-30")
    )
    validation_rows = []
    valid_pe_recent = (
        ep_unlagged.loc[recent_mask].notna()
        & pe_inverse.loc[recent_mask].notna()
        & np.isfinite(pe_inverse.loc[recent_mask])
    )
    validation_rows.extend(
        [
            validation_stat_row(
                "Quarterly unlagged E/P vs 1/P:E",
                ep_unlagged.loc[recent_mask],
                pe_inverse.loc[recent_mask],
                valid_pe_recent,
            ),
            validation_stat_row(
                "Quarterly lagged E/P vs 1/P:E",
                ep_quarterly.loc[recent_mask],
                pe_inverse.loc[recent_mask],
                valid_pe_recent,
            ),
        ]
    )

    per_nonempty_cols = []
    if per_inverse is not None:
        per_nonempty_cols = per_inverse.notna().sum()[lambda s: s > 0].index.tolist()
        valid_per_fiscal = per_inverse.notna() & ep_fiscal_year.notna() & np.isfinite(per_inverse)
        valid_per_ttm = per_inverse.notna() & ep_ttm.notna() & np.isfinite(per_inverse)
        valid_per_quarter = per_inverse.notna() & ep_quarterly.notna() & np.isfinite(per_inverse)
        validation_rows.insert(
            0,
            validation_stat_row(
                "Fiscal-year lagged E/P vs 1/PER(IFRS-연결)",
                ep_fiscal_year,
                per_inverse,
                valid_per_fiscal,
            ),
        )
        validation_rows.insert(
            1,
            validation_stat_row(
                "TTM lagged E/P vs 1/PER(IFRS-연결)",
                ep_ttm,
                per_inverse,
                valid_per_ttm,
            ),
        )
        validation_rows.insert(
            2,
            validation_stat_row(
                "Quarterly lagged E/P vs 1/PER(IFRS-연결)",
                ep_quarterly,
                per_inverse,
                valid_per_quarter,
            ),
        )
        if pe_inverse is not None:
            valid_per_pe = (
                per_inverse.notna()
                & pe_inverse.notna()
                & np.isfinite(per_inverse)
                & np.isfinite(pe_inverse)
            )
            validation_rows.append(
                validation_stat_row(
                    "1/P:E vs 1/PER(IFRS-연결)",
                    pe_inverse,
                    per_inverse,
                    valid_per_pe,
                )
            )
    validation_summary = pd.DataFrame(validation_rows)

    if per_nonempty_cols:
        validation_name = per_nonempty_cols[0]
        validation_series = pd.DataFrame(
            {
                "Fiscal-year lagged E/P": ep_fiscal_year[validation_name],
                "1/PER(IFRS-연결)": per_inverse[validation_name],
                "Quarterly lagged E/P (strategy)": ep_quarterly[validation_name],
                "TTM lagged E/P": ep_ttm[validation_name],
                "Quarterly 1/P:E": pe_inverse[validation_name],
            }
        ).loc[pd.Timestamp("2023-01-31") : pd.Timestamp("2025-06-30")]
    else:
        validation_name = "SK하이닉스"
        validation_series = pd.DataFrame(
            {
                "Quarterly lagged E/P (strategy)": ep_quarterly[validation_name],
                "Quarterly unlagged E/P": ep_unlagged[validation_name],
                "TTM lagged E/P": ep_ttm[validation_name],
                "Quarterly 1/P:E": pe_inverse[validation_name],
            }
        ).loc[pd.Timestamp("2023-10-31") : pd.Timestamp("2024-07-31")]

    manufacturing_flag = pd.Series({col: col in set(mfg_cols) for col in common_cols}, dtype=bool)
    ep_top20_a, ep_fin_share, ep_selected_a = top_n_returns(
        ep, forward_returns, mcap, share_reference=financial_flag
    )
    ep_top20_b, _, ep_selected_b = top_n_returns(
        ep, forward_returns, mcap, candidate_cols=non_fin_cols
    )
    bp_top20_a, bp_fin_share, bp_selected_a = top_n_returns(
        bp, forward_returns, mcap, share_reference=financial_flag
    )
    bp_top20_b, _, bp_selected_b = top_n_returns(
        bp, forward_returns, mcap, candidate_cols=non_fin_cols
    )
    ep_top20_mfg, _, _ = top_n_returns(
        ep, forward_returns, mcap, candidate_cols=mfg_cols
    )
    bp_top20_mfg, _, _ = top_n_returns(
        bp, forward_returns, mcap, candidate_cols=mfg_cols
    )

    universe_perf_rows = []
    universe_returns = {
        "E/P top20 A full market": ep_top20_a,
        "E/P top20 B financial excluded": ep_top20_b,
        "B/P top20 A full market": bp_top20_a,
        "B/P top20 B financial excluded": bp_top20_b,
    }
    for label, series in universe_returns.items():
        row = {"Portfolio": label}
        row.update(performance_metrics(series.loc[START_PERF:END_PERF]))
        universe_perf_rows.append(row)
    universe_perf = pd.DataFrame(universe_perf_rows)

    proxy_returns = {
        "E/P top20 manufacturing proxy": ep_top20_mfg,
        "B/P top20 manufacturing proxy": bp_top20_mfg,
    }
    manufacturing_proxy_perf_rows = []
    for label, series in proxy_returns.items():
        row = {"Portfolio": label}
        row.update(performance_metrics(series.loc[START_PERF:END_PERF]))
        manufacturing_proxy_perf_rows.append(row)
    manufacturing_proxy_perf = pd.DataFrame(manufacturing_proxy_perf_rows)

    universe_share = pd.DataFrame(
        {
            "E/P A top20 financial share": ep_fin_share,
            "B/P A top20 financial share": bp_fin_share,
        }
    ).loc[START_PERF:END_PERF]
    universe_nav = pd.DataFrame(
        {label: nav_from_returns(series.loc[START_PERF:END_PERF]) for label, series in universe_returns.items()}
    )

    snapshot_date = max(d for d in bp_selected_a if d <= pd.Timestamp("2026-05-29"))
    snapshot_names = bp_selected_a[snapshot_date]
    snapshot = pd.DataFrame(
        {
            "Date": snapshot_date.date().isoformat(),
            "Rank": range(1, len(snapshot_names) + 1),
            "Name": snapshot_names,
            "B/P": bp.loc[snapshot_date, snapshot_names].values,
            "MarketCap": mcap.loc[snapshot_date, snapshot_names].values,
            "IsFinancial": financial_flag.reindex(snapshot_names).fillna(False).values,
            "InManufacturingUniverse": manufacturing_flag.reindex(snapshot_names).fillna(False).values,
        }
    )

    # README 차트용 시계열 저장
    series_dir = BASE_DIR / "output" / "tables"
    series_dir.mkdir(parents=True, exist_ok=True)
    ic.join(ism.rename("ISM PMI"), how="left").to_csv(series_dir / "series_monthly_ic_pmi.csv", encoding="utf-8-sig")
    universe_nav.to_csv(series_dir / "series_top20_nav_full_vs_ex_financial.csv", encoding="utf-8-sig")
    universe_share.to_csv(series_dir / "series_top20_financial_share.csv", encoding="utf-8-sig")
    ep_quint_nav.to_csv(series_dir / "series_quintile_nav_ep.csv", encoding="utf-8-sig")
    bp_quint_nav.to_csv(series_dir / "series_quintile_nav_bp.csv", encoding="utf-8-sig")

    # README 차트용 시계열 저장
    series_dir = BASE_DIR / "output" / "tables"
    series_dir.mkdir(parents=True, exist_ok=True)
    ic.join(ism.rename("ISM PMI"), how="left").to_csv(series_dir / "series_monthly_ic_pmi.csv", encoding="utf-8-sig")
    universe_nav.to_csv(series_dir / "series_top20_nav_full_vs_ex_financial.csv", encoding="utf-8-sig")
    universe_share.to_csv(series_dir / "series_top20_financial_share.csv", encoding="utf-8-sig")
    ep_quint_nav.to_csv(series_dir / "series_quintile_nav_ep.csv", encoding="utf-8-sig")
    bp_quint_nav.to_csv(series_dir / "series_quintile_nav_bp.csv", encoding="utf-8-sig")

    figure_paths = save_figures(
        {"E/P": ep_quint_nav, "B/P": bp_quint_nav},
        ic,
        ism.loc[ic.index.min() : ic.index.max()],
        universe_share,
        universe_nav,
        validation_series,
        validation_name,
    )

    perf_all = pd.concat([perf_summary, universe_perf, manufacturing_proxy_perf], ignore_index=True)
    perf_all.to_csv(OUTPUT_DIR / "performance_summary.csv", index=False, encoding="utf-8-sig")
    ic_summary.to_csv(OUTPUT_DIR / "ic_summary.csv", index=False, encoding="utf-8-sig")
    ep_definition_summary.to_csv(
        OUTPUT_DIR / "ep_definition_summary.csv", index=False, encoding="utf-8-sig"
    )
    validation_summary.to_csv(OUTPUT_DIR / "validation_summary.csv", index=False, encoding="utf-8-sig")
    manufacturing_proxy_perf.to_csv(
        OUTPUT_DIR / "manufacturing_proxy_summary.csv", index=False, encoding="utf-8-sig"
    )
    snapshot.to_csv(OUTPUT_DIR / "full_market_bp_top20_snapshot.csv", index=False, encoding="utf-8-sig")

    ic_table = ic_summary.copy()
    perf_table = perf_summary.copy()
    universe_table = universe_perf.copy()
    proxy_table = manufacturing_proxy_perf.copy()
    ep_definition_table = ep_definition_summary.copy()
    validation_table = validation_summary.copy()

    strongest_bp_periods = (
        ic["B/P"].rolling(12, min_periods=6).mean().dropna().sort_values(ascending=False).head(3)
    )
    ep_ic_col = next((col for col in ic.columns if col.startswith("E/P")), "E/P")
    weakest_ep_periods = (
        ic[ep_ic_col].rolling(12, min_periods=6).mean().dropna().sort_values().head(3)
    )
    if per is not None and per_nonempty_cols:
        per_available = per.loc[:, per_nonempty_cols]
        per_period_text = (
            f"{per_available.dropna(how='all').index.min().date()} ~ "
            f"{per_available.dropna(how='all').index.max().date()}"
        )
        per_coverage_text = (
            f"`{per_source}` 사용, PER 관측치 {int(per_available.notna().sum().sum()):,}개, "
            f"PER 보유 종목 {len(per_nonempty_cols):,}개"
        )
    else:
        per_period_text = "PER 파일 없음"
        per_coverage_text = "PER 파일 없음"
    financial_quality_text = (
        f"{financial_source_text} 사용, 금융 플래그 {int(financial_flag.sum()):,}개"
    )
    if financial_is_exact:
        financial_method_text = (
            "금융 포함/제외 대조 실험은 DataGuide에서 추출한 금융 유니버스 파일을 우선 사용했습니다. "
            "A는 전체 시장, B는 전체 시총 상위 300종목 base를 먼저 고정한 뒤 금융 산업군만 제외한 유니버스입니다. "
            "제조업 유니버스는 금융 외 비제조업까지 제외하므로 참고 실험으로만 별도 표시했습니다."
        )
        financial_limitation_text = (
            "금융 산업군 매핑 파일을 사용했으므로, 제조업 근사보다 새 과제 Markdown의 본 실험 정의에 더 가깝습니다."
        )
    else:
        financial_method_text = (
            "금융 포함/제외 대조 실험은 새 과제 Markdown에 맞춰 A=전체 시장, B=금융 제외 유니버스로 계산하도록 바꿨습니다. "
            "둘 다 전체 시총 상위 300종목 base를 먼저 고정한 뒤, B에서만 금융 플래그 종목을 제외합니다. "
            "다만 현재 4주차 폴더에는 DataGuide 금융 산업군 매핑 파일이 없어, 실행 가능한 fallback으로 종목명 금융 키워드 플래그를 사용했습니다. "
            "제조업 유니버스는 금융 외 비제조업까지 제외하므로 참고 실험으로만 별도 표시했습니다."
        )
        financial_limitation_text = (
            "현재 수치는 정식 산업군 매핑이 아니라 종목명 fallback 기준입니다. 제출 전 `금융유니버스.csv` 또는 `금융업유니버스.csv`를 추가하면 코드가 자동으로 그 파일을 우선 사용합니다."
        )

    report = f"""# 반도체 밸류 팩터 국면 검증 결과

## 1. 사용 파일과 처리 범위

- 과제 지시문: `{ASSIGNMENT_MD}`
- 원본 Excel: `{RAW_XLSX}`
- 사용 CSV 폴더: `{DATA_DIR}`
- 가격·시총·재무 공통 기간: {price.index.min().date()} ~ {price.index.max().date()}
- 성과·IC 계산 기간: {START_PERF.date()} ~ {END_PERF.date()}
- 전체 종목 수: {len(common_cols):,}개
- 시클리컬 유니버스 교집합: {len(cyc_cols):,}개
- 시클리컬 유니버스 중 가격·재무 데이터에 없는 종목 수: {len(missing_cyc):,}개
- 제조업 유니버스 교집합: {len(mfg_cols):,}개
- 제조업 유니버스 중 가격·재무 데이터에 없는 종목 수: {len(missing_mfg):,}개
- 금융 분류: {financial_quality_text}
- PER 검증 데이터: {per_coverage_text}
- PER 검증 기간: {per_period_text}

## 2. 방법론 요약

원천 데이터에서 직접 `E/P = 이익 / 시가총액`, `B/P = 지배주주자본 / 시가총액`을 계산했습니다. 재무 데이터 단위가 시가총액보다 1,000배 크게 잡혀 있어 `시가총액 * 1000`을 분모로 사용했습니다. 분기 재무값은 룩어헤드를 막기 위해 1~3분기는 분기말 후 3개월, 4분기는 분기말 후 4개월 뒤부터 사용 가능하다고 보고 월말 데이터에 as-of 방식으로 매칭했습니다.

IC·성과·포트폴리오 구성에 쓰는 전략용 E/P는 원천 분기 데이터를 lag 적용한 `분기 지배주주순이익 / 시가총액`입니다. `TTM E/P`와 `결산연도 E/P`도 따로 계산하지만, 이 둘은 PER 검증 및 정의 비교용으로만 남겼습니다. 분기 E/P는 계절성·노이즈가 크고 이익 모멘텀 성격이 섞일 수 있으나, 새 과제 Markdown의 실측 기준에 맞춰 전략 팩터로 채택했습니다.

전략용 E/P 정의 선택을 확인하기 위해 같은 시클리컬 유니버스에서 분기·TTM·결산연도 E/P를 모두 비교했습니다.

{format_table(ep_definition_table, percent_cols={"Mean IC", "IC Std", "Positive IC Ratio", "Q5 CAGR", "Q5 MDD"})}

팩터는 매월 횡단면 1%/99% 윈저라이즈 후 z-score로 표준화했고, 다음 달 수정주가 수익률과의 스피어만 rank IC 및 5분위 동일가중 포트폴리오 성과를 계산했습니다. 본분석은 `시클리컬 유니버스.csv` 기준입니다.

{financial_method_text}

## 3. PER 결산 기준 검증

새로 추가된 `PER(IFRS-연결).csv`를 반영했습니다. 이 파일은 현재 {validation_name} 중심으로 들어 있어, PER 검증은 PER가 존재하는 종목·기간에 한정했습니다. `PER(IFRS-연결)`은 TTM이 아니라 결산 PER로 보이므로, 검증용으로만 `직전 결산연도 4개 분기 합산 이익 / 월말 시가총액`을 따로 만들고 Q4 결산값은 4개월 lag 후 사용했습니다. 전략용 분기 E/P와 TTM E/P는 아래 표에 같이 표시하되, `1/PER`와 완전히 일치해야 하는 검증 대상은 결산연도 E/P입니다. `P:E.csv`는 기존처럼 발표 lag가 없는 분기형 P/E 항목으로 별도 비교 행에 남겼습니다.

{format_table(validation_table, percent_cols={"Median Abs Diff", "Mean Abs Diff"})}

{validation_name} 기준으로 `1/PER`는 분기 E/P보다 결산연도 E/P와 훨씬 잘 맞습니다. 따라서 `PER(IFRS-연결)`은 결산 기준 sanity check로만 쓰고, IC·성과 분석은 원천 데이터로 직접 만든 분기 E/P를 사용했습니다. 완전히 일치하지 않는 잔차는 DataGuide PER가 순이익 총액이 아니라 EPS와 주식수 조정 등을 거친 완제품 지표이기 때문으로 봐야 합니다.

![E/P validation with PER]({figure_paths["validation"].name})

## 4. 시클리컬 유니버스 성과

{format_table(perf_table, percent_cols={"CAGR", "Ann.Vol", "MDD"})}

![Quintile NAV]({figure_paths["quintile_nav"].name})

## 5. IC와 국면 해석

{format_table(ic_table, percent_cols={"Mean IC", "IC Std", "Positive IC Ratio"})}

![Rolling IC with PMI]({figure_paths["rolling_ic"].name})

12개월 rolling IC 기준으로 B/P가 가장 강했던 시점은 {", ".join(d.strftime("%Y-%m") for d in strongest_bp_periods.index)}입니다. 반대로 {ep_ic_col} rolling IC가 가장 약했던 시점은 {", ".join(d.strftime("%Y-%m") for d in weakest_ep_periods.index)}입니다.

해석상 B/P는 순자산 대비 가격이 낮은 시클리컬 자산주를 포착하는 성격이 강하고, E/P는 발표 가능한 단일 분기 이익이 가격을 설명하는지를 봅니다. 이번 처리 데이터에서는 평균 IC와 상위 분위 NAV 모두 E/P가 B/P보다 높게 나왔습니다. 다만 분기 E/P는 이익 모멘텀 성격과 계절성 노이즈가 섞일 수 있으므로, 결론은 “한 팩터가 항상 우위”라기보다 rolling IC와 PMI를 같이 보면서 유효 국면을 나눠 해석하는 쪽이 더 맞습니다.

## 6. 금융 포함/제외 대조 실험

대조 실험은 매월 전체 시장의 시가총액 상위 300종목을 먼저 고정한 뒤, A는 그 안에서 바로 팩터 상위 20종목을 뽑고 B는 같은 top300 base에서 금융 플래그 종목만 제외한 뒤 팩터 상위 20종목을 뽑았습니다. 즉 B에서 다시 시총 상위 300종목을 뽑지 않습니다. 전체 시장 A 바스켓의 금융 플래그 비중도 함께 표시했습니다.

{format_table(universe_table, percent_cols={"CAGR", "Ann.Vol", "MDD"})}

![Full market financial share top20]({figure_paths["universe_share"].name})

![Full market vs financial excluded NAV]({figure_paths["universe_nav"].name})

대표 스냅샷은 `{snapshot_date.date()}`의 전체 시장 B/P 상위 20종목입니다. 이 표에는 각 종목의 금융 플래그를 표시했습니다. 전체 시장 결과와 금융 제외 결과를 나란히 보면, 저PBR·저PER 바스켓에 금융주가 얼마나 끼어드는지 확인할 수 있습니다.

제조업 유니버스 근사는 새 과제 Markdown처럼 참고용으로만 남겼습니다. 제조업은 금융뿐 아니라 유통·서비스 등 비제조업 전체를 함께 제외하므로, 금융 제외 효과와 동일하게 해석하면 안 됩니다.

{format_table(proxy_table, percent_cols={"CAGR", "Ann.Vol", "MDD"})}

{financial_limitation_text}

## 7. 제출 체크리스트

- Raw 데이터 Excel: 원본 파일 `{RAW_XLSX}` 그대로 제출
- 분석·검증 결과: 이 Markdown 파일 제출
- Code: `반도체_밸류팩터_국면검증_재현.ipynb` 제출

## 8. 남은 보완점

1. 현재 `PER(IFRS-연결).csv`는 {validation_name} 중심으로만 들어 있어 전체 종목 검증은 제한적입니다. 결산 PER를 전체 종목으로 확장하면 검증 표본을 넓힐 수 있습니다.
2. 금융 분류는 `금융유니버스.csv` 같은 DataGuide 금융 산업군 파일이 있으면 자동으로 우선 사용합니다. 현재 파일이 없으면 종목명 fallback이므로, 정식 제출 전 금융 산업군 파일을 추가해 다시 실행하는 것이 가장 안전합니다.
3. PER와 원천 E/P의 완전 일치를 확인하려면 DataGuide PER 산식에 쓰인 결산 EPS 또는 보통주 조정 주식수 항목까지 같이 받아 확인하는 것이 가장 정확합니다.
"""
    report_path = OUTPUT_DIR / "반도체_밸류팩터_국면검증_분석결과.md"
    report_path.write_text(report, encoding="utf-8")

    notebook_path = OUTPUT_DIR / "반도체_밸류팩터_국면검증_재현.ipynb"
    build_notebook(notebook_path)
    code_path = OUTPUT_DIR / "create_semiconductor_value_outputs.py"
    shutil.copy2(Path(__file__), code_path)

    manifest = {
        "report": str(report_path),
        "notebook": str(notebook_path),
        "code": str(code_path),
        "figures": {k: str(v) for k, v in figure_paths.items()},
        "tables": {
            "performance_summary": str(OUTPUT_DIR / "performance_summary.csv"),
            "ic_summary": str(OUTPUT_DIR / "ic_summary.csv"),
            "ep_definition_summary": str(OUTPUT_DIR / "ep_definition_summary.csv"),
            "validation_summary": str(OUTPUT_DIR / "validation_summary.csv"),
            "manufacturing_proxy_summary": str(OUTPUT_DIR / "manufacturing_proxy_summary.csv"),
            "full_market_snapshot": str(OUTPUT_DIR / "full_market_bp_top20_snapshot.csv"),
        },
        "counts": {
            "common_stock_count": len(common_cols),
            "cyclical_stock_count": len(cyc_cols),
            "missing_cyclical_count": len(missing_cyc),
            "manufacturing_stock_count": len(mfg_cols),
            "missing_manufacturing_count": len(missing_mfg),
            "financial_stock_count": int(financial_flag.sum()),
            "financial_source_is_exact": financial_is_exact,
        },
    }
    (OUTPUT_DIR / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
