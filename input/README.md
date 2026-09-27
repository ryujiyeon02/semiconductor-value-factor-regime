# 로컬 입력 데이터 (비공개)

`src/value_factor_regime.py`는 `input/processed_data/` 아래 CSV를 읽습니다. 원본 Excel(`4주차 데이터 원본.xlsx`, FnGuide DataGuide)과 가공 CSV는 재배포할 수 없어 Git에서 제외했습니다. 열 구조는 `sample_data/README.md`를 참고하세요.

필요 파일: `수정주가.csv`, `시가총액.csv`, `당기순이익(지배),분기.csv`, `자본총계(지배),분기.csv`, `P:E.csv`, `PER(IFRS-연결).csv`, `미국 ISM.csv`, `시클리컬 유니버스.csv`, `제조업유니버스.csv`

금융 산업군 파일(`금융유니버스.csv` 등)을 추가하면 코드가 종목명 키워드 근사 대신 그 파일을 자동으로 우선 사용합니다.
