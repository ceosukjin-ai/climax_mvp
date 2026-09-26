#!/usr/bin/env python3
"""SCS 논문 숫자 원장 (2026-09-21) — data/scs_master_80.csv 하나에서 전부 다시 계산.
출력: docs/SCS_숫자원장.md  (항목 | 원고 초안 값 | 정본 재계산 | 판정)
"""
import numpy as np, pandas as pd
from scipy import stats
import statsmodels.formula.api as smf
from statsmodels.stats.outliers_influence import variance_inflation_factor

d = pd.read_csv("data/scs_master_80.csv", encoding="utf-8-sig")
d["sun"] = d["sun"].astype(int); d["rise"] = d["Tmrt"] - d["Ta"]
S, H = d[d.sun == 1], d[d.sun == 0]
out = []
def row(sec, item, old, new, note=""):
    same = (str(old).strip() == str(new).strip())
    out.append((sec, item, old, new, "같음" if same else ("—" if old == "" else "**바뀜**"), note))

def cliff(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return ((a[:, None] > b[None, :]).sum() - (a[:, None] < b[None, :]).sum()) / (len(a) * len(b))
def ms(x): return f"{x.mean():.1f} ± {x.std(ddof=1):.1f}"
def sp(s, x, y="PET"):
    s = s[[x, y]].dropna(); r, p = stats.spearmanr(s[x], s[y]); return r, p, len(s)
def wi(s, x, y="PET"):
    s = s[[x, y, "LCZ"]].dropna()
    r, p = stats.spearmanr(s.groupby("LCZ")[x].rank(), s.groupby("LCZ")[y].rank()); return p
def fp(p):
    return "< 0.001" if p < 0.001 else f"{p:.3f}".rstrip("0") if p < 0.01 else f"{p:.2f}"
def sg(x, k=2): return f"{x:+.{k}f}".replace("-", "−")
def rho(r, p, n): return f"ρ {sg(r)}, p {fp(p)}, n {n}"
def perm_p(x, y, g, n=20000, seed=0):
    x, y, g = np.asarray(x, float), np.asarray(y, float), np.asarray(g)
    us = sorted(set(g)); xc, yc = x.copy(), y.copy()
    for u in us:
        m = g == u; xc[m] -= xc[m].mean(); yc[m] -= yc[m].mean()
    obs = np.corrcoef(xc, yc)[0, 1]; rg = np.random.default_rng(seed); c = 0
    for _ in range(n):
        ys = yc.copy()
        for u in us:
            m = g == u; ys[m] = rg.permutation(ys[m])
        if abs(np.corrcoef(xc, ys)[0, 1]) >= abs(obs) - 1e-12: c += 1
    return obs, (c + 1) / (n + 1)

# ---------- 1. 볕/그늘 (실측, 뷰팩터 무관)
sec = "5.1 도입 · Fig 6"
row(sec, "볕 PET 평균±SD (n)", "47.8 ± 2.7 (61)", f"{ms(S.PET)} ({len(S)})")
row(sec, "그늘 PET 평균±SD (n)", "40.7 ± 3.4 (19)", f"{ms(H.PET)} ({len(H)})")
row(sec, "PET 평균차", "7.1 K", f"{S.PET.mean()-H.PET.mean():.1f} K")
row(sec, "PET 중앙값차 (Fig 6a)", "7.7 K", f"{S.PET.median()-H.PET.median():.1f} K")
mw = stats.mannwhitneyu(S.PET, H.PET).pvalue
row(sec, "Mann–Whitney p", "< 10⁻⁷", "< 10⁻⁷" if mw < 1e-7 else f"{mw:.1e}")
row(sec, "Cliff δ (PET)", "0.83", f"{cliff(S.PET, H.PET):.2f}")
row(sec, "볕 Tmrt 평균±SD", "57.3 ± 7.0", ms(S.Tmrt))
row(sec, "그늘 Tmrt 평균±SD", "44.3 ± 5.1", ms(H.Tmrt))
row(sec, "Tmrt 평균차 / Cliff δ", "13.0 K / 0.88", f"{S.Tmrt.mean()-H.Tmrt.mean():.1f} K / {cliff(S.Tmrt, H.Tmrt):.2f}")
row(sec, "기온 평균차", "1.1 K", f"{S.Ta.mean()-H.Ta.mean():.1f} K")
row(sec, "PET ≥ 41: 볕 / 그늘", "60/61 · 3/19", f"{(S.PET>=41).sum()}/{len(S)} · {(H.PET>=41).sum()}/{len(H)}")
row(sec, "흑구 상승분 볕 / 그늘 (Tmrt−Ta)", "21.8 / 9.8 K", f"{S.rise.mean():.1f} / {H.rise.mean():.1f} K")
row(sec, "상승분 p (MWU)", "< 0.001", fp(stats.mannwhitneyu(S.rise, H.rise).pvalue))
# 분산 분해
g = d.groupby("LCZ").PET
eta = ((g.mean() - d.PET.mean())**2 * g.size()).sum() / ((d.PET - d.PET.mean())**2).sum()
row(sec, "PET 분산 중 LCZ 간 몫", "31 %", f"{eta*100:.0f} %")
rng = g.max() - g.min(); k = rng.idxmax()
row(sec, "가장 넓은 LCZ 범위", "15.5 K (LCZ 5, 37.6–53.1)", f"{rng.max():.1f} K (LCZ {k}, {g.min()[k]:.1f}–{g.max()[k]:.1f})")
row(sec, "LCZ 평균 사이 폭", "6.3 K", f"{g.mean().max()-g.mean().min():.1f} K")
for L, old in ((5, "+7.7"), (2, "+7.1"), (3, "+7.1"), (1, "")):
    a, b = S[S.LCZ == L].PET, H[H.LCZ == L].PET
    row(sec, f"LCZ {L} 안 볕−그늘 평균차", old, f"{a.mean()-b.mean():+.1f}")

# ---------- 2. 형태 4패널
sec = "5.1 형태 4패널 (Fig X)"
row(sec, "가로폭 범위", "2.2–66.1 m", f"{d.width_m.min():.1f}–{d.width_m.max():.1f} m")
row(sec, "(a) 폭–PET 전체", "ρ +0.12, p 0.31, n 73", rho(*sp(d, "width_m")))
row(sec, "(a) 폭–PET 볕", "ρ +0.04, p 0.76, n 55", rho(*sp(S, "width_m")))
row(sec, "(a) 폭 LCZ 내 p", "0.08", fp(wi(S, "width_m")))
row(sec, "(b) H/W–PET 전체", "ρ −0.24, p 0.04, n 73", rho(*sp(d, "hw_ratio")))
row(sec, "(b) H/W–PET 볕", "ρ −0.14, p 0.31, n 55", rho(*sp(S, "hw_ratio")))
row(sec, "(b) H/W LCZ 내 p", "0.53", fp(wi(S, "hw_ratio")))
row(sec, "(c) SVF–PET 전체", "ρ +0.48, p < 0.001, n 80", rho(*sp(d, "SVF")), "옛 값은 폐기된 파노라마 SVF 열")
row(sec, "(c) SVF–PET 볕", "ρ +0.32, p 0.01, n 61", rho(*sp(S, "SVF")), "〃")
row(sec, "(c) SVF LCZ 내 p", "0.25", fp(wi(S, "SVF")), "〃")
row(sec, "(d) SVF–Tmrt 전체", "ρ +0.34, p 0.002, n 80", rho(*sp(d, "SVF", "Tmrt")), "〃")
row(sec, "(d) SVF–Tmrt 볕", "ρ +0.02, p 0.88, n 61", rho(*sp(S, "SVF", "Tmrt")), "〃")
row(sec, "(d) SVF–Tmrt LCZ 내 p", "0.43", fp(wi(S, "SVF", "Tmrt")), "〃")
hot = H[H.PET >= 41]
row(sec, "뜨거운 그늘 (PET ≥ 41) 곳 · LCZ", "3곳 · LCZ 1", f"{len(hot)}곳 · LCZ " + ",".join(str(int(x)) for x in sorted(hot.LCZ.unique())))
row(sec, "  시각", "14:53–15:06", f"{hot.시각.str[11:16].min()}–{hot.시각.str[11:16].max()}")
row(sec, "  기온", "36.5–38.7 °C", f"{hot.Ta.min():.1f}–{hot.Ta.max():.1f} °C")
row(sec, "  PET", "46.7–49.0 °C", f"{hot.PET.min():.1f}–{hot.PET.max():.1f} °C")
row(sec, "  흑구 상승분", "10.9–17.5 K", f"{hot.rise.min():.1f}–{hot.rise.max():.1f} K")
# 다중회귀 (볕, 세 지표 모두 있는 곳)
m = S[["PET", "width_m", "hw_ratio", "SVF"]].dropna()
fit = smf.ols("PET ~ width_m + hw_ratio + SVF", m).fit()
X = m[["width_m", "hw_ratio", "SVF"]].assign(c=1.0).values
vif = max(variance_inflation_factor(X, i) for i in range(3))
row(sec, "다중회귀 R² / adj (n)", "0.095 / 0.042 (55)", f"{fit.rsquared:.3f} / {fit.rsquared_adj:.3f} ({len(m)})".replace("-", "−"))
row(sec, "  SVF 계수 p", "0.036", fp(fit.pvalues["SVF"]))
row(sec, "  최대 VIF", "< 1.4", "< 1.4" if vif < 1.4 else f"{vif:.2f}")
row(sec, "  폭–H/W ρ", "−0.87", sg(stats.spearmanr(m.width_m, m.hw_ratio)[0]))
row(sec, "  SVF–폭 / SVF–H/W ρ", "+0.19 / −0.25",
    sg(stats.spearmanr(m.SVF, m.width_m)[0]) + " / " + sg(stats.spearmanr(m.SVF, m.hw_ratio)[0]))

# ---------- 2b. 5.1 결정요인 (근린 고정효과 순열검정)
sec = "5.1 결정요인 (근린 내 순열검정)"
q = d[["SVF", "Tmrt", "Ts", "PET", "sun", "LCZ"]].dropna()
r_in, p_in = perm_p(q.SVF, q.Tmrt, q.LCZ)
row(sec, "SVF→Tmrt 전체 r", "+0.377", sg(np.corrcoef(q.SVF, q.Tmrt)[0, 1], 3), "옛 값은 폐기된 파노라마 SVF")
row(sec, "SVF→Tmrt 근린 내 r / 순열 p", "0.308 / 0.006", f"{r_in:.3f} / {fp(p_in)}", "〃")
fe = smf.ols("Tmrt ~ SVF + Ts + sun + C(LCZ)", q).fit()
row(sec, "Tmrt ~ SVF+Ts+볕+근린: SVF p", "0.149", fp(fe.pvalues.SVF), "OLS 고정효과")
row(sec, "  같은 모형: 볕 계수 (p)", "", f"{sg(fe.params.sun, 1)} K ({fp(fe.pvalues.sun)})")
row(sec, "  같은 모형: Ts 계수 (p)", "", f"{sg(fe.params.Ts, 2)} ({fp(fe.pvalues.Ts)})")
q2 = d[["Ts", "PET", "LCZ"]].dropna()
r2, p2 = perm_p(q2.Ts, q2.PET, q2.LCZ)
row(sec, "Ts→PET 전체 r", "+0.629", sg(np.corrcoef(q2.Ts, q2.PET)[0, 1], 3))
row(sec, "Ts→PET 근린 내 r / 순열 p", "0.514 / 0.0002", f"{r2:.3f} / {fp(p2)}")
OLDNB = {5: "+0.321", 2: "+0.175", 1: "+0.377", 3: "+0.414", 4: "+0.216"}
for L in range(1, 6):
    z = q[q.LCZ == L]
    row(sec, f"LCZ {L} 안 SVF→Tmrt r", OLDNB[L], sg(np.corrcoef(z.SVF, z.Tmrt)[0, 1], 3), "〃")

# ---------- 3. 보충 S2
sec = "보충 S2"
row(sec, "SVF–Ta 볕", "ρ +0.30, p 0.019", (lambda r: f"ρ {sg(r[0])}, p {fp(r[1])}")(stats.spearmanr(*S[["SVF", "Ta"]].dropna().T.values)))
s2 = S[["PET", "SVF", "Ta", "LCZ"]].dropna()
tot = smf.ols("PET ~ SVF", s2).fit(); a = smf.ols("Ta ~ SVF", s2).fit(); b = smf.ols("PET ~ SVF + Ta", s2).fit()
ind = a.params.SVF * b.params.Ta
row(sec, "매개: 총효과 (p)", "+7.84 (0.031)", f"{sg(tot.params.SVF)} ({fp(tot.pvalues.SVF)})")
row(sec, "  직접효과 (p)", "+3.62 (0.29)", f"{sg(b.params.SVF)} ({fp(b.pvalues.SVF)})")
row(sec, "  간접(기온 경로) / 비율", "+4.23 / 54 %", f"{sg(ind)} / {ind/tot.params.SVF*100:.0f} %")
rs = lambda v: stats.rankdata(v)
ex = lambda y: y - np.polyval(np.polyfit(rs(s2.Ta), y, 1), rs(s2.Ta))
pr = stats.pearsonr(ex(rs(s2.SVF)), ex(rs(s2.PET)))
row(sec, "편상관 SVF–PET | Ta", "+0.20 (0.13)", f"{sg(pr[0])} ({fp(pr[1])})")
mm = smf.mixedlm("PET ~ SVF", s2, groups=s2.LCZ).fit()
row(sec, "혼합모형 SVF β (p)", "+4.74 (0.27)", f"{sg(mm.params.SVF)} ({fp(mm.pvalues.SVF)})")

# ---------- 4. 표 (LCZ별)
sec = "표 C / 표 2 (LCZ별)"
OLD = {1: ("0.83", "0.16", "0.018"), 2: ("0.71", "0.25", "0.036"), 3: ("0.66", "0.31", "0.025"),
       4: ("0.78", "0.18", "0.007"), 5: ("0.80", "0.17", "0.028")}
for L in range(1, 6):
    q = d[d.LCZ == L]
    row(sec, f"LCZ {L} n (볕)", "", f"{len(q)} ({q.sun.sum()})")
    row(sec, f"LCZ {L} SVF 중앙", OLD[L][0], f"{q.SVF.median():.2f}", "옛 값은 폐기 열")
    row(sec, f"LCZ {L} BVF 중앙", OLD[L][1], f"{q.BVF.median():.2f}", "〃")
    row(sec, f"LCZ {L} TVF 평균", OLD[L][2], f"{q.TVF.mean():.3f}", "〃")
    row(sec, f"LCZ {L} 폭 중앙 / H/W 중앙", "", f"{q.width_m.median():.1f} / {q.hw_ratio.median():.2f}")
    row(sec, f"LCZ {L} 건물높이 H 중앙 (최대)", "", f"{q.H_m.median():.1f} ({q.H_m.max():.1f})", "9/26 건축물대장 층수")
    row(sec, f"LCZ {L} PET 평균 (범위폭)", "", f"{q.PET.mean():.1f} ({q.PET.max()-q.PET.min():.1f})")
    row(sec, f"LCZ {L} 표고 범위", "", f"{q.표고_m.min():.0f}–{q.표고_m.max():.0f} m")
row(sec, "방법절: 표고 LCZ 4 / LCZ 2", "2–3 m / 24–101 m",
    f"{d[d.LCZ==4].표고_m.min():.0f}–{d[d.LCZ==4].표고_m.max():.0f} m / {d[d.LCZ==2].표고_m.min():.0f}–{d[d.LCZ==2].표고_m.max():.0f} m",
    "옛 값은 출처 불명 DEM")

# ---------- 5. 측정 조건 (방법·한계)
sec = "방법 · 한계"
row(sec, "태양고도 범위", "46–67°", f"{d.태양고도.min():.0f}–{d.태양고도.max():.0f}°")
row(sec, "기온 범위", "32.0–38.7 °C", f"{d.Ta.min():.1f}–{d.Ta.max():.1f} °C")
row(sec, "풍속 범위", "0.0–2.2 m s⁻¹", f"{d.v.min():.1f}–{d.v.max():.1f} m s⁻¹")
row(sec, "H/W 중앙 · ≥1 · ≥2", "0.54 · 12/73 · 5", f"{d.hw_ratio.median():.2f} · {(d.hw_ratio>=1).sum()}/{d.hw_ratio.notna().sum()} · {(d.hw_ratio>=2).sum()}")
row(sec, "SVF 중앙 (범위), n", "", f"{d.SVF.median():.2f} ({d.SVF.min():.2f}–{d.SVF.max():.2f}), {d.SVF.notna().sum()}")

# ---------- 6. Fig 2 흐름도: 기하 SVF 검증 — 서버 엔진 값이 필요
sec = "Fig 2 · 방법 (기하 SVF 검증)"
import os
if os.path.isfile("data/geo_svf_80.csv"):
    gq = pd.read_csv("data/geo_svf_80.csv", encoding="utf-8-sig").merge(d[["측정ID", "SVF"]], on="측정ID").dropna(subset=["geo_svf","SVF"])
    e = gq.geo_svf - gq.SVF
    row(sec, "기하 SVF vs 어안: MAE", "0.108", f"{e.abs().mean():.3f}", "엔진 9/20판 vs 일관 정의 SVF")
    row(sec, "  bias", "+0.022", sg(e.mean(), 3))
    row(sec, "  r (n)", "0.69 (79)", f"{stats.pearsonr(gq.geo_svf, gq.SVF)[0]:.2f} ({len(gq)})")
else:
    row(sec, "기하 SVF vs 어안: MAE / bias / r", "0.108 / +0.022 / 0.69", "서버 재실행 대기",
        "scripts/geo_svf_check_80.py — 옛 값은 9/14 SVF(×1.0585) 기준")

# ---------- 7. Fig 6b 기준조건
sec = "Fig 6b (기준조건 = 실측 중앙값)"
row(sec, "기온 / 습도 / 풍속 중앙값", "35.0 / 47.6 / 0.5", f"{d.Ta.median():.1f} / {d.RH.median():.1f} / {d.v.median():.1f}")
row(sec, "태양고도 중앙값", "65°", f"{d.태양고도.median():.0f}°")

with open("docs/SCS_숫자원장.md", "w", encoding="utf-8") as fh:
    fh.write("# SCS 논문 숫자 원장\n\n자동 생성: `scripts/scs_numbers.py` ← `data/scs_master_80.csv` (`scripts/scs_master.py`)\n\n")
    cur = None
    for sec, item, old, new, j, note in out:
        if sec != cur:
            fh.write(f"\n## {sec}\n\n| 항목 | 원고 초안 | 정본 재계산 | 판정 | 비고 |\n|---|---|---|---|---|\n"); cur = sec
        fh.write(f"| {item} | {old} | {new} | {j} | {note} |\n")
print(open("docs/SCS_숫자원장.md", encoding="utf-8").read())
