# 합성 샘플 데이터

FnGuide DataGuide에서 받은 재무·가격 원본은 공개하지 않습니다. 이 폴더의 CSV는 `input/processed_data/`가 기대하는 열 구조를 보여주는 **가짜 값**이며 README 결과 산출에 사용하지 않았습니다.

```bash
python scripts/generate_sample_data.py
```

| 샘플 | 원본 입력 | 구조 |
| --- | --- | --- |
| `수정주가_sample.csv`, `시가총액_sample.csv` | `수정주가.csv`, `시가총액.csv` | `Date`(YYYY.M.D) × 종목명, 시총 백만원 |
| `당기순이익(지배),분기_sample.csv`, `자본총계(지배),분기_sample.csv` | 같은 이름의 원본 | 월별 행에 해당 분기 값이 반복 (천원). 코드가 분기말 기준으로 묶은 뒤 lag를 적용 |
| `미국 ISM_sample.csv` | `미국 ISM.csv` | `Date`, `미국 ISM(Pt)` |
| `시클리컬 유니버스_sample.csv` | `시클리컬 유니버스.csv`, `제조업유니버스.csv` | 1행 코드, 2행 종목명 |

원본에는 이 밖에 `P:E.csv`, `PER(IFRS-연결).csv`(삼성전자만 존재)가 검증용으로 쓰입니다.
