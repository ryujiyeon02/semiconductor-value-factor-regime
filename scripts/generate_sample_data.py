"""공개용 합성 샘플 데이터 생성기. 입력 열 구조만 같고 값은 가짜다."""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "sample_data"
OUT.mkdir(exist_ok=True)
rng = np.random.default_rng(11)
dates = pd.date_range("2024-01-31", periods=18, freq="ME")
names = [f"샘플종목{i:02d}" for i in range(1, 26)]
codes = [f"A{100000 + i:06d}" for i in range(1, 26)]
idx = [f"{d.year}.{d.month}.{d.day}" for d in dates]

price = pd.DataFrame(20000 * np.cumprod(1 + rng.normal(0.01, 0.09, (len(dates), len(names))), axis=0), index=idx, columns=names).round(0)
mcap = (price * rng.integers(5_000, 80_000, len(names))).div(1e6).round(0)  # 백만원 단위 가정
q_income = pd.DataFrame(rng.normal(5e6, 3e6, (len(dates), len(names))), index=idx, columns=names).round(0)  # 천원 단위 가정
q_equity = pd.DataFrame(rng.normal(8e7, 2e7, (len(dates), len(names))), index=idx, columns=names).round(0)
for df, name in [(price, "수정주가_sample.csv"), (mcap, "시가총액_sample.csv"),
                 (q_income, "당기순이익(지배),분기_sample.csv"), (q_equity, "자본총계(지배),분기_sample.csv")]:
    df.index.name = "Date"
    df.to_csv(OUT / name, encoding="utf-8-sig")
pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "미국 ISM(Pt)": rng.normal(50, 3, len(dates)).round(1)}).to_csv(
    OUT / "미국 ISM_sample.csv", index=False, encoding="utf-8-sig")
pd.DataFrame([["코드", *codes[:10]], ["코드명", *names[:10]]]).to_csv(
    OUT / "시클리컬 유니버스_sample.csv", index=False, header=False, encoding="utf-8-sig")
print(sorted(p.name for p in OUT.glob("*.csv")))
