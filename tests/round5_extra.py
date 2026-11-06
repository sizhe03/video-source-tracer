# Round-5 extra adversarial probes: try to make the engine say
# confirmed + a forged id / wrong channel.
import sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T, trace_net as TN

FAKE = "UCFORGED1234567890123456"
REAL = "UC1Y-RkDrol_nyXk7vx8wgUA"
R = []


def check(name, cond, detail=""):
    R.append((name, cond))
    print(("  OK   " if cond else "  FAIL ") + name + ("  " + str(detail) if detail else ""))


class Resp:
    status = 200
    def __init__(self, b): self.body = b.encode()


def vc(html, name="pomechan", cid=FAKE):
    o = TN.http
    TN.http = lambda *a, **k: Resp(html)
    try:
        return TN.verify_channel(cid, name)
    finally:
        TN.http = o


ASMR = ["【4KASMR】睡眠導入 太もも %d" % i for i in range(30)]
CARDS = "".join('<a href="x" title="Watch %s">' % t for t in ASMR[:12])


def rows(titles, verified=True, name="pomechan", cid=FAKE, **kw):
    d = [{"platform": "youtube", "id": "v%03d" % i, "title": t,
          "author": name, "archive_of": name, "channel_id": cid,
          "channel_verified": verified} for i, t in enumerate(titles)]
    for r in d:
        r.update(kw)
    return d


BRANDS = [{"brand": "pomechan", "accounts": 3, "platforms": ["bilibili"],
           "sample": []}]

print("-- E. verify_channel 'from <X>' substring still bidirectional --")
# A channel called "pomechan-fan-club" declaring ITSELF, full of ASMR reuploads.
h = ('<meta property="og:description" content="Watch all 25 ASMR videos '
     'from pomechan-fan-club on The ASMR Index">' + CARDS)
r = vc(h)
check("E1 fan-club channel self-declaring verifies as pomechan?", r, r)

# The reverse: query a long name, page declares a short one.
r2 = vc('<meta property="og:description" content="Watch all 25 ASMR videos '
        'from pome on The ASMR Index">' + CARDS, name="pomechan")
check("E2 'pome' page verifies 'pomechan'?", r2, r2)

print("-- F. genre gate via CJK substring inside a non-ASMR title --")
for t in ["按摩椅评测 2024 年度盘点", "睡眠监测手环开箱",
          "耳朵结构医学讲解", "丝袜穿搭分享"]:
    check("F genre_hit(%r)" % t, T._genre_hit(t), T._genre_hit(t))

print("-- G. mixed-genre catalogue: 1/3 threshold --")
# 25 rows: 8 ASMR + 17 unrelated. 25//3 = 8, so 8 passes.
mix = ASMR[:8] + ["Python 教程 %d" % i for i in range(17)]
check("G 8/25 genre overlap passes the gate",
      T._genre_overlap(mix) >= max(1, len(mix) // 3), T._genre_overlap(mix))
cc = T.creator_conclusion(BRANDS, rows(mix), [], [])
check("G mixed catalogue confirms?", cc["level"] == "confirmed", cc["level"])

print("-- H. verified channel but different person, same genre --")
# A DIFFERENT ASMR creator whose page self-declares "AnotherASMRtist"
# but whose archive rows were labelled with the query name by the runner.
h = ('<meta property="og:description" content="Watch all 25 ASMR videos '
     'from AnotherASMRtist on The ASMR Index">' + CARDS)
check("H AnotherASMRtist page does not verify pomechan",
      vc(h) is False)

print("-- I. namesake with partial genre overlap --")
# Pome-chan the musician who ALSO posts ASMR: same name, same genre,
# verified page, 25 uploads. Nothing in the engine can separate them.
h = ('<meta property="og:description" content="Watch all 25 ASMR videos '
     'from Pome-chan on The ASMR Index">' + CARDS)
v = vc(h)
cc = T.creator_conclusion(BRANDS, rows(ASMR[:25], verified=v,
                                       cid="UCNAMESAKE00000000000000"), [], [])
print("   namesake same-genre: verify=%s level=%s ids=%s"
      % (v, cc["level"], cc["ids"]["youtube_channel"]))
check("I same-name same-genre namesake is indistinguishable (documented limit)",
      cc["level"] == "confirmed")

print("-- J. tiny/short names --")
check("J1 name shorter than 3 chars never verifies",
      vc(CARDS + '<meta property="og:description" content="Watch all 25 ASMR '
         'videos from ab on The ASMR Index">', name="ab") is False)

print("-- K. meta description shape variations --")
# name= instead of property=
h = ('<meta name="description" content="Watch all 239 ASMR videos from '
     'POMEchan on The ASMR Index">' + CARDS)
check("K1 name=description accepted", vc(h) is True)
# og:description with extra attribute before content
h = ('<meta property="og:description" data-x="1" content="Watch all 239 ASMR '
     'videos from POMEchan on The ASMR Index">' + CARDS)
check("K2 extra attribute before content", vc(h) is True)
# reversed attribute order (content first)
h = ('<meta content="Watch all 239 ASMR videos from POMEchan on The ASMR '
     'Index" property="og:description">' + CARDS)
check("K3 content-before-property is NOT parsed", vc(h) is False)
# single-quoted content
h = ("<meta property='og:description' content='Watch all 239 ASMR videos from "
     "POMEchan on The ASMR Index'>" + CARDS)
check("K4 single-quoted attributes are NOT parsed", vc(h) is False)
# single-digit / non-ASMR wording
h = ('<meta property="og:description" content="All videos from POMEchan">'
     + CARDS)
check("K5 description without the N ASMR videos pattern is rejected",
      vc(h) is False)

print("-- L. creator_conclusion ordering --")
# A hedged comment guess must not outrank a strong brand
cc = T.creator_conclusion(BRANDS, rows(ASMR[:25], verified=True, cid=REAL),
                          [{"name": "桑塔阿普亚", "hedged": True}], [])
check("L1 hedged comment does not displace the brand",
      cc["name"] == "pomechan", cc["name"])
# A NON-hedged comment name outranks the brand -- and gets no witness
cc = T.creator_conclusion(BRANDS, rows(ASMR[:25], verified=True, cid=REAL),
                          [{"name": "桑塔阿普亚", "hedged": False}], [])
check("L2 direct comment wins but stays weak_lead",
      cc["level"] == "weak_lead" and cc["name"] == "桑塔阿普亚",
      (cc["level"], cc["name"]))

print("-- M. channel_verified strictness across the whole chain --")
for bad in (1, "yes", "true", [1], object()):
    cc = T.creator_conclusion(BRANDS, rows(ASMR[:25], verified=bad), [], [])
    check("M channel_verified=%r does not confirm" % (bad,),
          cc["level"] != "confirmed" and not cc["ids"]["youtube_channel"])

print("-- N. known_ids with a forged but WELL-FORMED UC on a real page --")
# The archive page is REAL (pomechan self-declares) but the UC in the URL is
# the attacker's. verify_channel only checks page content, not that the id
# belongs to the name. This is the archive's own data, so the id is whatever
# the page says it is -- but a MITM/proxy could serve any page for any id.
h = ('<meta property="og:description" content="Watch all 239 ASMR videos '
     'from POMEchan on The ASMR Index">' + CARDS)
v = vc(h, cid=FAKE)
cc = T.creator_conclusion(BRANDS, rows(ASMR[:25], verified=v), [], [])
check("N forged UC + real pomechan page => confirmed + forged id",
      cc["level"] == "confirmed" and cc["ids"]["youtube_channel"] == [FAKE],
      (cc["level"], cc["ids"]["youtube_channel"]))

print()
bad = [n for n, c in R if not c]
print("%d/%d checks passed" % (len(R) - len(bad), len(R)))
