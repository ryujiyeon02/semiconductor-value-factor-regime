"""README용 대표 차트와 PMI 국면별 IC 표를 만든다.

선행: python src/value_factor_regime.py  (output/tables/series_*.csv 생성)
실행: python src/build_portfolio_charts.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "output" / "tables"
CHARTS = ROOT / "output" / "charts"
CHARTS.mkdir(parents=True, exist_ok=True)

EP, BP, BENCH = "#982A2F", "#2F6DA3", "#8C8C8C"
INK, MUTED = "#222222", "#666666"
for font_file in Path.home().glob("Library/Fonts/Pretendard-*.otf"):
    fm.fontManager.addfont(str(font_file))
plt.rcParams.update({
    "font.family": ["Pretendard", "AppleGothic"], "axes.unicode_minus": False,
    "axes.edgecolor": "#BBBBBB", "axes.titleweight": "bold", "axes.titlesize": 12,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": "#E6E6E6",
    "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
    "savefig.dpi": 160, "savefig.bbox": "tight", "figure.facecolor": "white",
})

# 1. 월별 IC와 PMI 국면
ic = pd.read_csv(TABLES / "series_monthly_ic_pmi.csv", index_col=0, parse_dates=True)
ic["PMI_change"] = ic["ISM PMI"].diff()
# ISM PMI는 다음 달 첫 영업일에 발표되므로, 실제로 알 수 있던 값은 한 달 전 PMI다.
ic["PMI_known"] = ic["ISM PMI"].shift(1)
ic["PMI_known_change"] = ic["PMI_known"].diff()


def label_regime(level, change):
    if pd.isna(level) or pd.isna(change):
        return np.nan
    side = "PMI≥50" if level >= 50 else "PMI<50"
    move = "상승" if change > 0 else "하락"
    return f"{side}·{move}"


order = ["PMI<50·하락", "PMI<50·상승", "PMI≥50·상승", "PMI≥50·하락"]
rows = []
for basis, lvl, chg in [("동월 PMI(사후 해석)", "ISM PMI", "PMI_change"), ("전월 PMI(당시 관측 가능)", "PMI_known", "PMI_known_change")]:
    regime = [label_regime(a, b) for a, b in zip(ic[lvl], ic[chg])]
    tmp = ic.assign(regime=regime).dropna(subset=["regime"])
    for reg in order:
        sub = tmp[tmp["regime"] == reg]
        for factor in ["E/P", "B/P"]:
            x = sub[factor].dropna()
            rows.append({"basis": basis, "regime": reg, "factor": factor, "months": len(x),
                         "mean_ic": x.mean(), "t_stat": x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))})
regime_tbl = pd.DataFrame(rows)
regime_tbl.to_csv(TABLES / "ic_by_pmi_regime.csv", index=False, encoding="utf-8-sig")
print(regime_tbl.round(3).to_string())

roll = ic[["E/P", "B/P"]].rolling(12, min_periods=12).mean()

# 2. 대표 차트
perf = pd.read_csv(ROOT / "output" / "results" / "performance_summary.csv")
fig = plt.figure(figsize=(16, 8.6))
gs = fig.add_gridspec(3, 2, width_ratios=[2.1, 1], height_ratios=[1.6, 0.8, 1.35], hspace=0.55, wspace=0.18)

ax1 = fig.add_subplot(gs[0, 0])
exp = ic["ISM PMI"] >= 50
for start, end, is_exp in zip(ic.index[:-1], ic.index[1:], exp.iloc[:-1]):
    if not is_exp:
        ax1.axvspan(start, end, color="#F3E3E4", linewidth=0)
ax1.plot(roll.index, roll["E/P"], color=EP, linewidth=2, label="E/P")
ax1.plot(roll.index, roll["B/P"], color=BP, linewidth=2, label="B/P")
ax1.axhline(0, color="#999999", linewidth=0.8)
ax1.set_title("A. 12개월 이동평균 IC — 우위 팩터가 계속 바뀐다 (음영: ISM PMI < 50)", loc="left")
ax1.set_ylabel("Spearman rank IC")
ax1.legend(frameon=False, ncol=2, loc="upper left")

ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
ax2.plot(ic.index, ic["ISM PMI"], color=INK, linewidth=1.3)
ax2.axhline(50, color="#999999", linewidth=0.8, linestyle="--")
ax2.fill_between(ic.index, ic["ISM PMI"], 50, where=ic["ISM PMI"] < 50, color="#E7C3C5", linewidth=0)
ax2.set_title("B. 미국 ISM 제조업 PMI", loc="left")

ax3 = fig.add_subplot(gs[0:2, 1])
sub = regime_tbl[regime_tbl["basis"] == "전월 PMI(당시 관측 가능)"]
y = np.arange(len(order))
for off, factor, color in [(0.19, "E/P", EP), (-0.19, "B/P", BP)]:
    vals = sub[sub["factor"] == factor].set_index("regime").loc[order]
    ax3.barh(y + off, vals["mean_ic"] * 100, height=0.36, color=color, label=factor)
    for yi, (m, t) in zip(y + off, zip(vals["mean_ic"], vals["t_stat"])):
        ax3.text(m * 100 + (0.2 if m >= 0 else -0.2), yi, f"{m*100:+.1f}%  (t={t:.1f})",
                 va="center", ha="left" if m >= 0 else "right", fontsize=9, color=INK)
counts = sub[sub["factor"] == "E/P"].set_index("regime").loc[order, "months"]
ax3.set_yticks(y)
ax3.set_yticklabels([f"{r}\n({n}개월)" for r, n in zip(order, counts)], fontsize=10)
ax3.axvline(0, color="#999999", linewidth=0.8)
ax3.set_xlim(-5.5, 8.5)
ax3.invert_yaxis()
ax3.set_xlabel("평균 월별 IC (%)")
ax3.set_title("C. PMI 국면별 평균 IC\n(전월 PMI 기준 = 당시 알 수 있던 정보)", loc="left")
ax3.legend(frameon=False, loc="lower right")
ax3.grid(axis="y", visible=False)

ax4 = fig.add_subplot(gs[2, :])
labels = ["E/P top20", "B/P top20"]
a = perf.set_index("Portfolio")
metrics = [("CAGR", "CAGR"), ("MDD", "MDD"), ("Sharpe", "Sharpe")]
x = np.arange(len(labels) * len(metrics))
names, full_vals, ex_vals = [], [], []
for mname, col in metrics:
    for lab in labels:
        full_vals.append(a.loc[f"{lab} A full market", col])
        ex_vals.append(a.loc[f"{lab} B financial excluded", col])
        names.append(f"{lab}\n{mname}")
scale = [100 if m != "Sharpe" else 1 for m, _ in metrics for _ in labels]
full_plot = [v * s for v, s in zip(full_vals, scale)]
ex_plot = [v * s for v, s in zip(ex_vals, scale)]
# 서로 다른 단위를 한 축에 섞지 않도록 지표별로 작은 패널을 나눈다.
ax4.remove()
sub_gs = gs[2, :].subgridspec(1, 3, wspace=0.25)
for k, (mname, col) in enumerate(metrics):
    ax = fig.add_subplot(sub_gs[0, k])
    unit = 100 if mname != "Sharpe" else 1
    xs = np.arange(len(labels))
    fv = [a.loc[f"{lab} A full market", col] * unit for lab in labels]
    ev = [a.loc[f"{lab} B financial excluded", col] * unit for lab in labels]
    ax.bar(xs - 0.19, fv, width=0.36, color=BENCH, label="A 전체 시장 (금융 포함)")
    ax.bar(xs + 0.19, ev, width=0.36, color=[EP, BP], label="B 금융 제외")
    for xi, v in zip(list(xs - 0.19) + list(xs + 0.19), fv + ev):
        ax.text(xi, v, f"{v:.1f}%" if unit == 100 else f"{v:.2f}", ha="center",
                va="bottom" if v >= 0 else "top", fontsize=9, color=INK)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.grid(axis="x", visible=False)
    title = {"CAGR": "CAGR (%)", "MDD": "MDD (%)", "Sharpe": "Sharpe"}[mname]
    ax.set_title(("D. 시총 상위 300 안에서 금융주를 빼면 — " if k == 0 else "") + title, loc="left")
    if mname == "MDD":
        ax.set_ylim(min(fv + ev) * 1.2, 0)
    else:
        ax.set_ylim(0, max(fv + ev) * (1.7 if mname == "Sharpe" else 1.25))
    if k == 2:
        from matplotlib.patches import Patch
        ax.legend(handles=[Patch(color=BENCH, label="A 금융 포함"), Patch(color=EP, label="B 금융 제외 (E/P)"),
                           Patch(color=BP, label="B 금융 제외 (B/P)")], frameon=False, fontsize=8.5, loc="upper center", ncol=3, columnspacing=0.8, handlelength=1.2)

fig.suptitle("반도체·시클리컬 밸류 팩터: 언제 E/P가, 언제 B/P가 통했나 (2007-01 ~ 2026-06, 월간)",
             x=0.01, ha="left", fontsize=15, fontweight="bold")
fig.text(0.01, 0.005, "A~C: 시클리컬 121종목, 재무는 1~3분기 3개월·4분기 4개월 lag 후 사용. D: 전체 시장 시총 상위 300종목 내 top20 동일가중, "
         "금융 여부는 종목명 키워드 근사(정식 산업분류 아님).", fontsize=9, color=MUTED)
fig.savefig(CHARTS / "hero_ic_pmi_financial.png")
plt.close(fig)
print("saved", CHARTS / "hero_ic_pmi_financial.png")
