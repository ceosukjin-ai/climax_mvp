# Google Maps Platform 상용 라이선스 문의서 (2026-08-21)

## 0. 보내기 전에 — 읽고 판단하실 것

**작성 원칙: "앞으로 하려는 것"을 묻는 형식으로 썼습니다.**
과거·현재의 이용 방식을 문서로 자세히 진술하면, 그 메일이 그대로 위반 인정 자료가 됩니다.
그래서 아래 초안은 **의도된 용도(intended use)를 설명하고 사전 확인을 구하는** 형태입니다.
사실과 다르지 않으면서(위성 학생 모델 학습은 실제로 보류 상태), 불필요한 자백은 피합니다.

**보낼 곳 (둘 다 하시길 권합니다)**

1. **영업 채널** — https://mapsplatform.google.com/contact-sales/
   (규모가 작으면 답이 안 올 수 있습니다. 그래도 "문의한 기록"이 남는 게 중요합니다.)
2. **Cloud Console 지원** — Google Cloud Console → Support → Case 생성.
   결제 계정이 붙어 있으면 이쪽이 실제 답변률이 높습니다.

**기대치**: 표준 답변은 "약관대로 하라"일 가능성이 높습니다. 그래도 보내는 이유는 —
① 혹시 있을 라이선스 경로 확인 ② 답이 없거나 불가하면 **원천 교체 결정의 근거 기록**
③ 나중에 문제가 생겼을 때 "선의로 확인을 구했다"는 증빙.

**답이 오면 반드시 보관하세요.** 특허·투자 실사와 B2G 계약에서 요구될 수 있습니다.

---

## 1. 영문 메일 초안 (그대로 복사해 쓰실 수 있습니다)

**Subject:** Licensing inquiry — Street View Static API for pedestrian heat-risk analysis (South Korea)

Dear Google Maps Platform team,

I am the CEO of ClimaX Co., Ltd., a climate-adaptation data company based in Busan, South Korea. We operate a public-safety mobile service that estimates pedestrian-level thermal comfort so that elderly and other heat-vulnerable residents can be warned before heat illness occurs.

I am writing to ask whether a commercial licence exists that would permit the use described below, and to obtain written guidance **before** we proceed further.

**Intended use**

For a given coordinate, our server resolves a panorama ID via the Street View Metadata API and retrieves five views (front / back / left / right / up) from the Street View Static API. A semantic-segmentation model computes three scalar spatial indices from those images:

- Sky View Factor — the fraction of sky visible overhead
- Green View Index — the fraction of vegetation in the horizontal field of view
- Building View Index — the fraction of built surface in the horizontal field of view

These three numbers are inputs to a radiative-transfer model that estimates mean radiant temperature and, from it, a physiologically equivalent temperature for the pedestrian at that location. **No Street View imagery is displayed to end users, redistributed, or retained as imagery.** Only the three scalars are retained, keyed by panorama ID, so that a location does not need to be re-analysed on every visit.

**Specific questions**

1. **Derived indices.** Clause 3.2.3(c) prohibits creating content based on Google Maps Content, with "construct an index of tree locations within a city from Street View imagery" given as an example. Is there any licence tier, written permission, or public-interest programme under which computing and retaining the three scalar indices above would be permitted? If so, what are the terms and pricing?

2. **Retention of derived values.** Clause 3.2.3(b) permits caching only as expressly allowed in the Service Specific Terms, which name panorama IDs (A.3). Is retention of a derived scalar keyed to a panorama ID — as distinct from caching Content itself — permitted, and if not, is a licensed exception available?

3. **Machine learning.** Clause 3.2.3(c)(vii) prohibits using Google Maps Content to train, test, validate or fine-tune machine-learning models. We would like to train a model that estimates pedestrian thermal comfort from satellite and open geospatial data alone, for regions where Street View coverage does not exist. Is there any licence under which Street View–derived values could serve as training labels for such a model? **We have suspended this work pending your answer.**

4. **Public-safety or research programmes.** Does Google operate any programme — academic, non-profit, humanitarian, or public-safety — that grants terms different from the standard Maps Platform agreement for this kind of heat-mortality-prevention work? If so, how do we apply?

5. **Attribution.** Where Google-derived values inform a result but no imagery is shown, we currently credit "Imagery © Google" on a Data Sources screen in the app and in the website footer. Please confirm whether this satisfies the attribution requirements, or advise the correct form.

**Context that may be relevant.** Heat mortality in South Korea is concentrated among elderly residents; our work is intended to reduce it and is discussed with local government as a public-safety measure. If the answer to the questions above is that no such licence exists, we will migrate to alternative imagery sources rather than operate outside your terms — so a clear "no" is genuinely useful to us, and we would appreciate one in writing.

Thank you for your time.

정숙진 (Sukjin Jung)
CEO, ClimaX Co., Ltd. (㈜클라이맥스)
200 Gobun-ro, Yeonje-gu, Busan, Republic of Korea
ceosukjin@gmail.com
Business registration 185-87-03973

---

## 2. 무엇을 물었는지 (한국어 요약)

| # | 질문 | 왜 묻나 |
|---|---|---|
| 1 | SVF/GVI/BVI 산출·보관을 허용하는 라이선스가 있는가 | 3.2.3(c)(v)의 "수목 인덱스 구축" 예시와 구조가 같아 정면으로 걸림 |
| 2 | panoId에 붙은 **파생 스칼라** 보관은 캐싱 금지의 예외가 되는가 | A.3는 panoId만 허용. 파생값은 회색이 아니라 미허용 쪽 |
| 3 | 위성 학생 모델의 **학습 라벨**로 쓸 수 있는가 | 3.2.3(c)(vii) 정면. **"보류 중"이라고 명시** |
| 4 | 공익·연구 프로그램이 있는가 | 폭염 사망 예방이라는 성격을 명시적으로 올림 |
| 5 | 이미지 미표시 상황에서 저작자 표시는 어떻게 하나 | A.2 이행을 서면으로 확인받아 둠 |

마지막 문단이 핵심입니다 — **"안 된다면 원천을 교체하겠다"**고 먼저 밝혔습니다.
답을 받아내기 쉬워지고, 답이 없더라도 우리 의도가 기록에 남습니다.

## 3. 답변별 다음 행동

| 답변 | 다음 행동 |
|---|---|
| 라이선스 있음 | 조건·비용 확인 → 계약. 특허 명세의 데이터 원천 기술도 그대로 유지 가능 |
| 불가 (서면) | 그 메일을 보관하고 **원천 교체 로드맵 실행** (SVF/BVI → V-World, GVI → Mapillary/자체촬영) |
| 무응답 (4주) | 불가와 동일하게 처리. 발송 기록 자체를 실사 대비 증빙으로 보관 |

## 4. 발송 기록 (보내신 뒤 채워 주세요)

| 항목 | 내용 |
|---|---|
| 발송일 | |
| 채널 | ☐ contact-sales ☐ Cloud Console Support (Case #) |
| 회신일 | |
| 회신 요지 | |
| 보관 위치 | |
