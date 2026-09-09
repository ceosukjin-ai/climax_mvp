#!/usr/bin/env python3
"""간토 .osm.pbf에서 도쿄 건물만 뽑아 backend/data/buildings 타일로 적재.
⚠️ 맥 터미널에서 실행(RAM·시간 여유 필요). 사전: pip3 install osmium

사용:
  cd ~/Desktop/climax_mvp
  pip3 install osmium
  python3 scripts/extract_tokyo_tiles.py
입력: data/kanto.osm.pbf (Geofabrik 간토)  출력: backend/data/buildings/<타일>.json
"""
import json, math, os, time
import osmium

W, S, E, N = 139.56, 35.53, 139.92, 35.82           # 도쿄 23구 코어 bbox
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PBF = os.path.join(ROOT, "data", "kanto.osm.pbf")
OUT_DIR = os.path.join(ROOT, "backend", "data", "buildings")
KEEP = ("building", "building:material", "building:levels", "height", "gro_flo_co", "name")

os.makedirs(OUT_DIR, exist_ok=True)
t0 = time.time()

# --- pass1: 도쿄권 노드 위치 ---
loc = {}
class NH(osmium.SimpleHandler):
    def node(self, n):
        l = n.location
        if l.valid() and W-0.01 <= l.lon <= E+0.01 and S-0.01 <= l.lat <= N+0.01:
            loc[n.id] = (l.lat, l.lon)
print("pass1: 노드 수집(수십초)…", flush=True)
NH().apply_file(PBF)
print(f"  노드 {len(loc):,} / {time.time()-t0:.0f}s", flush=True)

# --- pass2: 건물 → 타일 dict ---
def tile_key(lat, lon):
    return f"{int(math.floor(lat*100))}_{int(math.floor(lon*100))}"

tiles = {}
class WH(osmium.SimpleHandler):
    def __init__(self): super().__init__(); self.n = 0
    def way(self, w):
        if "building" not in w.tags: return
        coords = [loc[r] for r in (nd.ref for nd in w.nodes) if r in loc]
        if len(coords) < 4: return
        if not any(W <= x <= E and S <= y <= N for y, x in coords): return
        tags = {k: w.tags[k] for k in KEEP if k in w.tags}
        geom = [{"lat": round(y, 6), "lon": round(x, 6)} for y, x in coords]
        tk = tile_key(geom[0]["lat"], geom[0]["lon"])
        tiles.setdefault(tk, {})[w.id] = {"id": w.id, "geometry": geom, "tags": tags}
        self.n += 1
        if self.n % 200000 == 0: print(f"  건물 {self.n:,}", flush=True)
print("pass2: 건물 조립·타일링…", flush=True)
wh = WH(); wh.apply_file(PBF)
print(f"  건물 {wh.n:,} / {time.time()-t0:.0f}s", flush=True)

# --- 타일 저장(기존 병합, 압축) ---
written = 0
for tk, byid in tiles.items():
    fp = os.path.join(OUT_DIR, tk + ".json")
    if os.path.isfile(fp):
        try:
            for el in (json.load(open(fp, encoding="utf-8")) or {}).get("elements") or []:
                byid.setdefault(el.get("id", id(el)), el)
        except Exception: pass
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"elements": list(byid.values())}, f, ensure_ascii=False, separators=(",", ":"))
    written += 1
print(f"완료: 건물 {wh.n:,} → 타일 {written}개 / {time.time()-t0:.0f}s")
