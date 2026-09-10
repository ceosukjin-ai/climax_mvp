#!/usr/bin/env python3
"""논문용 플로우차트 2장 (SVG + PNG). Noto Sans CJK KR."""
import html

FONT = "'Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',sans-serif"
C = {"in": "#6B7280", "in_bg": "#F3F4F6", "phys": "#2F5D8A", "phys_bg": "#E8F0F8",
     "ai": "#C2410C", "ai_bg": "#FEF0E7", "mix": "#4B5563", "mix_bg": "#FFFFFF",
     "out": "#15803D", "out_bg": "#EAF5EC", "txt": "#111827", "sub": "#4B5563", "line": "#374151"}


def esc(s):
    return html.escape(s, quote=False)


class SVG:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{FONT}">',
                      '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                      f'<path d="M0,0 L10,5 L0,10 z" fill="{C["line"]}"/></marker>'
                      '<marker id="ar_ai" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                      f'<path d="M0,0 L10,5 L0,10 z" fill="{C["ai"]}"/></marker></defs>',
                      f'<rect width="{w}" height="{h}" fill="#FFFFFF"/>']

    def box(self, x, y, w, h, fill, stroke, r=10, sw=1.6, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, size=15, color=C["txt"], weight="normal", anchor="middle"):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{esc(s)}</text>')

    def lines(self, x, y, ls, size=14, color=C["txt"], lh=None, anchor="middle", weight="normal"):
        lh = lh or size * 1.45
        for i, s in enumerate(ls):
            self.text(x, y + i * lh, s, size, color, weight, anchor)

    def arrow(self, x1, y1, x2, y2, color=C["line"], marker="ar", sw=1.8, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}" marker-end="url(#{marker})"{d}/>')

    def path(self, d, color=C["line"], marker="ar", sw=1.8, dash=None):
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" marker-end="url(#{marker})"{dd}/>')

    def tag(self, x, y, s, color):
        w = 30
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="13" fill="{color}"/>')
        self.text(x, y + 5, s, 14, "#FFFFFF", "bold")

    def save(self, path):
        self.parts.append("</svg>")
        open(path, "w", encoding="utf-8").write("\n".join(self.parts))


# ───────────────────────── 그림 1: 구조 ─────────────────────────
def fig1(path):
    W, H = 1180, 860
    s = SVG(W, H)
    s.text(W / 2, 40, "물리 뼈대 + 잔차 AI — 추론 흐름 (스트리트뷰 없이 좌표 → 체감 → 행동)", 20, C["txt"], "bold")

    # 입력 밴드
    s.box(40, 70, W - 80, 150, C["in_bg"], C["in"], dash="6 4")
    s.text(60, 95, "입력  ·  거리영상·현장센서 없음 — 커버리지 밖 골목에도 존재하는 것만", 14, C["sub"], "bold", "start")
    inputs = [("건물지도 (GIS)", ["기하 SVF · 가로폭 W · H/W", "태양방향 그늘 (ray-cast)"]),
              ("위성 (Sentinel-2)", ["NDVI → 녹시율 GVI"]),
              ("기상 (관측소 / Open-Meteo)", ["기온 Ta · 습도 RH · 풍속 v"]),
              ("시각 · 좌표", ["태양고도 · 방위"])]
    bw, gap = 250, 20
    x0 = (W - (bw * 4 + gap * 3)) / 2
    xs = []
    for i, (t, ls) in enumerate(inputs):
        x = x0 + i * (bw + gap)
        s.box(x, 112, bw, 90, "#FFFFFF", C["in"])
        s.text(x + bw / 2, 140, t, 15, C["txt"], "bold")
        s.lines(x + bw / 2, 165, ls, 13, C["sub"])
        xs.append(x + bw / 2)

    # ① 물리 엔진
    py = 262
    s.box(120, py, W - 240, 120, C["phys_bg"], C["phys"], sw=2)
    s.tag(150, py + 26, "1", C["phys"])
    s.text(172, py + 31, "물리 엔진 — 동네를 가리지 않는 전이의 뼈대", 16, C["phys"], "bold", "start")
    steps = ["일사 추정", "지면·벽 온도\n(열관성 · 재질)", "6방향 복사 → MRT", "PET_phys"]
    sw_, sg = 190, 40
    sx0 = (W - (sw_ * 4 + sg * 3)) / 2
    for i, st in enumerate(steps):
        x = sx0 + i * (sw_ + sg)
        s.box(x, py + 52, sw_, 50, "#FFFFFF", C["phys"], r=8)
        ls = st.split("\n")
        if len(ls) == 1:
            s.text(x + sw_ / 2, py + 83, ls[0], 14, C["phys"], "bold")
        else:
            s.text(x + sw_ / 2, py + 74, ls[0], 14, C["phys"], "bold")
            s.text(x + sw_ / 2, py + 92, ls[1], 12, C["sub"])
        if i < 3:
            s.arrow(x + sw_ + 4, py + 77, x + sw_ + sg - 4, py + 77, C["phys"])
    for x in xs:
        s.arrow(x, 202, x, py - 4)

    # ② 잔차 AI
    ay = 430
    s.box(120, ay, 600, 150, C["ai_bg"], C["ai"], sw=2)
    s.tag(150, ay + 26, "2", C["ai"])
    s.text(172, ay + 31, "잔차 AI — 물리가 놓친 만큼만 학습", 16, C["ai"], "bold", "start")
    s.lines(150, ay + 62, ["ΔPET = f( 볕, SVF, GVI, Ta, RH, v, 태양고도, MRT, PET_phys, NDVI )",
                           "Ridge 회귀 · 표준화 계수 10개 (해석 가능)",
                           "학습 정답: 걸어서 잰 실측 PET − PET_phys (부산 5근린 80점)",
                           "피처에 좌표 없음 → 장소가 아니라 형태로 일반화"], 13, C["txt"], anchor="start")

    # 확신도
    s.box(760, ay, 300, 150, "#FFFFFF", C["ai"], dash="6 4")
    s.text(910, ay + 30, "확신도 c ∈ [0, 1]", 15, C["ai"], "bold")
    s.lines(910, ay + 58, ["학습에서 본 형태와의 거리(표준화 z)",
                           "가까우면 c = 1, 멀수록 c ↓",
                           "터무니없는 외삽만 c = 0",
                           "→ 안 가본 골목도 형태가 같으면 보정"], 12.5, C["sub"])
    # arrows into AI
    s.arrow(W / 2, py + 120 + 4, W / 2, ay - 4)                      # phys → (합류점)
    s.path(f"M {W/2} {ay-24} L 420 {ay-24} L 420 {ay-4}", C["line"], sw=1.8)
    s.path(f"M {W/2} {ay-24} L 910 {ay-24} L 910 {ay-4}", C["line"], sw=1.8)
    s.text(W / 2 + 8, ay - 30, "PET_phys · MRT · 입력 피처", 12, C["sub"], anchor="start")

    # ③ 결합
    my = 630
    s.box(300, my, 580, 64, C["mix_bg"], C["mix"], sw=2)
    s.tag(330, my + 32, "3", C["mix"])
    s.text(590, my + 30, "PET = PET_phys + c · ΔPET", 18, C["txt"], "bold")
    s.text(590, my + 52, "c가 작을수록 물리 값으로 수렴 — 끄는 것이 아니라 줄이는 것", 12.5, C["sub"])
    s.arrow(420, ay + 150 + 4, 420, my - 4, C["ai"], "ar_ai")
    s.arrow(910, ay + 150 + 4, 910, my - 4, C["ai"], "ar_ai", dash="5 4")
    s.text(432, ay + 175, "ΔPET", 12, C["ai"], anchor="start")
    s.text(922, ay + 175, "c", 12, C["ai"], anchor="start")

    # ④ 출력
    oy = 740
    s.box(120, oy, W - 240, 80, C["out_bg"], C["out"], sw=2)
    s.tag(150, oy + 26, "4", C["out"])
    s.text(172, oy + 31, "출력 — 숫자가 아니라 행동", 16, C["out"], "bold", "start")
    outs = ["위험 등급 (극심 / 주의 / 안전)", "→", "언제 나갈까 · 어느 길로 · 어디서 쉴까", "→", "앱 · 지자체 돌봄 채널"]
    ox = [330, 505, 660, 845, 960]
    for x, t in zip(ox, outs):
        s.text(x, oy + 60, t, 14, C["out"] if t != "→" else C["sub"], "bold" if t != "→" else "normal")
    s.arrow(590, my + 64 + 4, 590, oy - 4, C["out"])
    s.save(path)


# ───────────────────────── 그림 2: 학습·검증 순환 ─────────────────────────
def fig2(path):
    W, H = 1180, 620
    s = SVG(W, H)
    s.text(W / 2, 40, "학습 · 검증 · 확장 순환 — AI는 예측기이자 물리의 진단기", 20, C["txt"], "bold")
    nodes = [("걸어서 실측", ["흑구 · 열화상 · 360° 카메라", "5근린 80점 (골목 포함)", "정답 PET · Tmrt · Ts"], C["in"], C["in_bg"]),
             ("엔진 소급 실행", ["같은 좌표·시각·기상으로", "PET_phys 산출", "잔차 = 실측 − PET_phys"], C["phys"], C["phys_bg"]),
             ("잔차 학습", ["Ridge, 피처 10개", "(스트리트뷰 무관)", "확신도 함수 c 정의"], C["ai"], C["ai_bg"]),
             ("근린 LOSO 검증", ["5근린 중 1개 빼고 학습 →", "뺀 근린에서 시험 × 5", "전체 / 커버리지 내 / 골목 보고"], C["mix"], "#FFFFFF"),
             ("계수 해석", ["어느 형태에서 틀리나", "→ 물리 결함 진단", "예: 그늘 지면 열관성"], C["ai"], C["ai_bg"])]
    bw, bh, gap = 200, 120, 30
    x0 = (W - (bw * 5 + gap * 4)) / 2
    y = 110
    cx = []
    for i, (t, ls, col, bg) in enumerate(nodes):
        x = x0 + i * (bw + gap)
        s.box(x, y, bw, bh, bg, col, sw=2)
        s.text(x + bw / 2, y + 30, t, 16, col, "bold")
        s.lines(x + bw / 2, y + 58, ls, 12.5, C["sub"])
        cx.append(x + bw / 2)
        if i < 4:
            s.arrow(x + bw + 4, y + bh / 2, x + bw + gap - 4, y + bh / 2)

    # 비교표 (LOSO 아래)
    ty = 262
    s.box(cx[3] - 190, ty, 380, 118, "#FFFFFF", C["mix"], r=8, dash="5 4")
    rows = [("비교 (근린 LOSO, n=80)", "R²", "MAE", "극심"),
            ("순수 ML  F0~F3", "< 0", "3.5~4.9", "—"),
            ("물리 단독", "−0.14", "3.50", "57/63"),
            ("물리 + 잔차 AI", "0.33~0.48", "2.4~2.7", "63/63")]
    for j, (a, b, c, d) in enumerate(rows):
        yy = ty + 24 + j * 24
        bold = "bold" if j in (0, 3) else "normal"
        col = C["ai"] if j == 3 else (C["sub"] if j == 0 else C["txt"])
        s.text(cx[3] - 178, yy, a, 12.5, col, bold, "start")
        s.text(cx[3] + 45, yy, b, 12.5, col, bold)
        s.text(cx[3] + 115, yy, c, 12.5, col, bold)
        s.text(cx[3] + 170, yy, d, 12.5, col, bold)
    s.arrow(cx[3], y + bh + 4, cx[3], ty - 4, C["mix"])

    # 루프 A: 계수 해석 → 물리 수정 → 재학습 (위쪽 회귀)
    ly = 470
    s.box(cx[1] - 130, ly, 260, 70, C["phys_bg"], C["phys"], sw=2)
    s.text(cx[1], ly + 28, "물리 엔진 수정", 15, C["phys"], "bold")
    s.text(cx[1], ly + 50, "그늘 지면 열관성 0.74 · 현장풍속 이중감쇠 제거", 12, C["sub"])
    s.path(f"M {cx[4]} {y+bh+4} L {cx[4]} {ly+35} L {cx[1]+130+4} {ly+35}", C["phys"])
    s.text(cx[4] - 12, ly + 22, "물리 결함 → 물리로 돌려보냄", 12, C["phys"], anchor="end")
    s.path(f"M {cx[1]} {ly-4} L {cx[1]} {y+bh+4}", C["phys"])
    s.text(cx[1] + 10, (ly + y + bh) / 2 + 40, "재학습", 12, C["phys"], anchor="start")
    s.text(cx[1] + 10, (ly + y + bh) / 2 + 58, "(볕 계수 −0.42 → +0.04:", 11.5, C["sub"], anchor="start")
    s.text(cx[1] + 10, (ly + y + bh) / 2 + 74, "그늘 잔차가 물리로 흡수)", 11.5, C["sub"], anchor="start")

    # 루프 B: 저확신 형태 지도 → 다음 실측
    s.box(cx[0] - 100, ly, 200, 70, C["ai_bg"], C["ai"], sw=2, dash="6 4")
    s.text(cx[0], ly + 28, "저확신 형태 지도", 15, C["ai"], "bold")
    s.text(cx[0], ly + 50, "좁은 골목 · 아침/저녁 → 다음 실측", 12, C["sub"])
    s.path(f"M {cx[2]} {y+bh+4} L {cx[2]} {ly+90} L {cx[0]} {ly+90} L {cx[0]} {ly+70+4}", C["ai"], "ar_ai", dash="5 4")
    s.text(cx[2] + 10, ly + 86, "c 낮은 형태 = 데이터 없는 곳 = 취약층 골목", 12, C["ai"], anchor="start")
    s.path(f"M {cx[0]} {ly-4} L {cx[0]} {y+bh+4}", C["ai"], "ar_ai")
    s.text(cx[0] + 10, (ly + y + bh) / 2 + 40, "걸어가서 채움", 12, C["ai"], anchor="start")
    s.save(path)


if __name__ == "__main__":
    fig1("fig1_architecture_ko.svg")
    fig2("fig2_learning_loop_ko.svg")
    print("ok")
