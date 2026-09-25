#!/usr/bin/env python3
"""일본 주택 「건축시기 × 구조」 기본값 — 令和5年 住宅・土地統計調査 (2026-09-25).

사용자가 築年을 모를 때 쓰는 **동네(대도시) 단위 기본값**을 만든다.
표: 基本集計 7-1 「住宅の建て方(4区分)、構造(4区分)、階数(9区分)、建築の時期(14区分)別住宅数
     －全国、都道府県、21大都市」 (statsDataId 0004021718). 東京都区部·大阪市·名古屋市·福岡市 등이 들어 있다.
e-Stat API 는 appId(무료 등록) 가 필요하다 → .env.prod 에 ESTAT_APP_ID=... (대표님이 직접 넣기).

  docker run --rm --env-file ~/climax_mvp/infra/ncp/.env.prod -v ~/climax_mvp:/repo climax-backend:latest \
    python3 /repo/scripts/load_jp_housing_prior.py
출력: /repo/backend/app/data/jp_housing_prior_2023.json (배포 이미지에 app/ 만 복사되므로 app 아래)  {지역: {구조: {pre1980,s55,h4,h11: 비율}, "n": 호수}}
분류 이름으로 골라서 쓴다(코드를 짐작하지 않는다) — 실행하면 분류표를 먼저 찍는다.
"""
from __future__ import annotations
import json, os, re, sys
from collections import defaultdict

URL = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
SID = "0004021718"
OUT = "/repo/backend/app/data/jp_housing_prior_2023.json"   # 이미지에는 app/ 만 들어간다


def era_of(name: str) -> str | None:
    if "不詳" in name or "総数" in name:
        return None
    ys = [int(y) for y in re.findall(r"(\d{4})", name)]
    if not ys:
        return None
    if "以前" in name:
        return "pre1980" if ys[0] <= 1980 else None
    start = ys[0]
    if start <= 1980 and (len(ys) < 2 or ys[1] <= 1980):
        return "pre1980"
    if start <= 1990:
        return "s55"
    if start <= 2000:      # 1991~95, 1996~2000 — H4(1992)·H11(1999) 경계가 칸 안에 있다. 다수 쪽(H4)으로.
        return "h4"
    return "h11"


def struct_of(name: str) -> str | None:
    if "総数" in name or "非木造" in name:
        # 非木造 = RC·철골·기타의 합계 칸이다. 「木造」 글자가 들어 있어 목조로 잘못 셌다(2026-09-25 수정) —
        # 그러면 목조가 전체와 같아지고 '전체'도 비목조를 두 번 센다.
        return None
    if "木造" in name:
        return "wood"                 # 木造·防火木造
    if "コンクリート" in name:
        return "rc"
    if "鉄骨" in name:
        return "steel"
    return "other"


def main():
    import httpx
    app = os.environ.get("ESTAT_APP_ID", "")
    if not app:
        print("ESTAT_APP_ID 없음 — e-Stat 에서 appId 를 받아 .env.prod 에 넣을 것"); return
    r = httpx.get(URL, params={"appId": app, "statsDataId": SID, "metaGetFlg": "Y",
                               "cntGetFlg": "N", "limit": 100000}, timeout=120.0)
    js = r.json()["GET_STATS_DATA"]
    st = js["RESULT"]
    if str(st.get("STATUS")) not in ("0", "1"):
        print("e-Stat 오류:", st); return
    sd = js["STATISTICAL_DATA"]
    objs = sd["CLASS_INF"]["CLASS_OBJ"]
    names = {}
    for o in objs:
        cl = o["CLASS"] if isinstance(o["CLASS"], list) else [o["CLASS"]]
        names[o["@id"]] = (o["@name"], {c["@code"]: c["@name"] for c in cl})
        print(f"  분류 {o['@id']:6s} {o['@name']}: {', '.join(list(names[o['@id']][1].values())[:16])}")
    def find(key):
        return next((i for i, (n, _) in names.items() if key in n), None)
    c_str, c_era, c_area = find("構造"), find("建築の時期"), "area"
    others = [i for i in names if i not in (c_str, c_era, c_area, "tab", "time")]
    print(f"\n구조={c_str} 시기={c_era} 나머지(총수만 씀)={others}")
    tot_code = {i: next((k for k, v in names[i][1].items() if "総数" in v), None) for i in others}
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    vals = sd["DATA_INF"]["VALUE"]
    for v in vals:
        if any(v.get("@" + i) != tot_code[i] for i in others if tot_code[i]):
            continue
        try:
            x = float(v["$"])
        except (ValueError, KeyError):
            continue
        s = struct_of(names[c_str][1].get(v.get("@" + c_str), ""))
        e = era_of(names[c_era][1].get(v.get("@" + c_era), ""))
        area = names[c_area][1].get(v.get("@area"), v.get("@area"))
        if s is None or e is None:
            continue
        agg[area][s][e] += x
        agg[area]["all"][e] += x
    out = {}
    for area, d in agg.items():
        out[area] = {}
        for s, eras in d.items():
            n = sum(eras.values())
            if n > 0:
                out[area][s] = {k: round(eras.get(k, 0) / n, 3) for k in ("pre1980", "s55", "h4", "h11")}
                out[area][s]["n"] = int(n)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"source": "令和5年住宅・土地統計調査 基本集計 7-1 (statsDataId 0004021718)",
               "era_map": "〜1980=pre1980, 1981〜90=s55, 1991〜2000=h4(경계 칸은 다수쪽), 2001〜=h11",
               "areas": out}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✅ {OUT} — 지역 {len(out)}곳")
    for k in [a for a in out if any(w in a for w in ("全国", "区部", "大阪", "名古屋", "福岡", "札幌", "仙台", "広島", "京都"))]:
        a = out[k].get("all", {})
        w = out[k].get("wood", {})
        print(f"  {k:10s} 전체 ~1980 {a.get('pre1980', 0):.0%} · 1981~90 {a.get('s55', 0):.0%} · "
              f"1991~2000 {a.get('h4', 0):.0%} · 2001~ {a.get('h11', 0):.0%}   | 목조 ~1980 {w.get('pre1980', 0):.0%}")


if __name__ == "__main__":
    main()
