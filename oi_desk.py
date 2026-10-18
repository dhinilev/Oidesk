#!/usr/bin/env python3
"""
OI Desk for Kotak Neo  (read-only: it NEVER places orders)

  python oi_desk.py debug            # login + print raw responses (use this first)
  python oi_desk.py call             # one 9:20-style call, saves lock.json
  python oi_desk.py run              # waits for 09:20, makes the call, then tracks till 15:15

Setup:   pip install kotakneoapi pyotp
Env vars (or a .env file next to this script):
  KOTAK_CONSUMER_KEY  KOTAK_MOBILE (+91...)  KOTAK_UCC  KOTAK_MPIN  KOTAK_TOTP_SECRET
Optional: INDEX=NIFTY|BANKNIFTY (default NIFTY)   GAP=0.3 (GIFT Nifty gap %, manual)   FII=1|0|-1
"""
import os, sys, json, time, datetime as dt, urllib.request, xml.etree.ElementTree as ET

LOCK = "lock.json"
CFG = {"NIFTY": dict(step=50, spot="Nifty 50", name="NIFTY"),
       "BANKNIFTY": dict(step=100, spot="Nifty Bank", name="BANKNIFTY")}
POS = "rally surge gain gains rise rises jumps record high upbeat positive strong buying optimism rate cut ceasefire".split()
NEG = "fall falls drop drops slump crash selloff sell-off weak war tariff inflation fear fears negative losses plunge rate hike downgrade".split()

def load_env():
    if os.path.exists(".env"):
        for l in open(".env"):
            if "=" in l and not l.strip().startswith("#"):
                k, v = l.strip().split("=", 1); os.environ.setdefault(k, v.strip().strip('"'))
load_env()
idx = CFG[os.environ.get("INDEX", "NIFTY").upper()]
STEP = idx["step"]

# ---------- Kotak login ----------
def login():
    import pyotp
    from neo_api_client import NeoAPI
    c = NeoAPI(consumer_key=os.environ["KOTAK_CONSUMER_KEY"], environment="prod")
    c.totp_login(mobile_number=os.environ["KOTAK_MOBILE"], ucc=os.environ["KOTAK_UCC"],
                 totp=pyotp.TOTP(os.environ["KOTAK_TOTP_SECRET"]).now())
    c.totp_validate(mpin=os.environ["KOTAK_MPIN"])
    return c

def pick(d, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, "", "-"):
            try: return float(d[k])
            except Exception: pass
    return default

def rows_of(resp):
    if isinstance(resp, dict): resp = resp.get("data", resp.get("message", []))
    return resp if isinstance(resp, list) else []

# ---------- data fetch ----------
def spot_price(c):
    r = c.quotes(instrument_tokens=[{"instrument_token": idx["spot"], "exchange_segment": "nse_cm"}],
                 quote_type="ltp", isIndex=True)
    rr = rows_of(r)
    if not rr: raise RuntimeError("No spot quote: " + str(r)[:300])
    return pick(rr[0], "ltp", "last_traded_price", "iv")

def option_tokens(c, spot):
    """Find CE/PE tokens for the nearest expiry around ATM from the scrip master."""
    import csv, io
    m = c.scrip_master(exchange_segment="nse_fo")
    text = m if isinstance(m, str) and "," in m and "\n" in m else None
    if text is None:
        url = m[0] if isinstance(m, list) else (m.get("filesPaths") or [m])[0] if isinstance(m, dict) else m
        text = urllib.request.urlopen(url).read().decode()
    rdr = csv.DictReader(io.StringIO(text))
    rdr.fieldnames = [f.strip().strip(";").lower() for f in rdr.fieldnames]
    atm = round(spot / STEP) * STEP
    cand = []
    for r in rdr:
        if r.get("psymbolname", "").strip().upper() != idx["name"]: continue
        ot = r.get("poptiontype", "").strip().upper()
        if ot not in ("CE", "PE"): continue
        k = float(r["dstrikeprice"]) / 100.0 if float(r["dstrikeprice"]) > 1e5 else float(r["dstrikeprice"])
        e = float(r["pexpirydate"])
        e = dt.datetime.utcfromtimestamp(e + 315513000) if e < 2e9 else dt.datetime.utcfromtimestamp(e)  # Kotak uses a 1980 epoch
        if e.date() < dt.date.today(): continue
        if abs(k - atm) <= 8 * STEP: cand.append((e, k, ot, r["psymbol"]))
    if not cand: raise RuntimeError("No option contracts found; run `debug` to inspect the scrip master.")
    near = min(x[0] for x in cand)
    return [(k, ot, t) for e, k, ot, t in cand if e == near], near

BASE = {}   # first snapshot of the day, used if the API gives no OI change
def chain(c, spot):
    toks, exp = option_tokens(c, spot)
    out = {}
    for i in range(0, len(toks), 40):
        part = toks[i:i + 40]
        r = c.quotes(instrument_tokens=[{"instrument_token": t, "exchange_segment": "nse_fo"} for _, _, t in part],
                     quote_type="all")
        for q in rows_of(r):
            tok = str(q.get("instrument_token") or q.get("tk") or "")
            for k, ot, t in part:
                if str(t) == tok:
                    oi = pick(q, "open_int", "oi", "open_interest", "openInterest", default=0)
                    ch = pick(q, "oi_change", "change_in_oi", "chng_in_oi", "oi_chg")
                    if ch is None:
                        prev = pick(q, "prev_open_int", "prev_oi")
                        ch = oi - prev if prev is not None else oi - BASE.setdefault((k, ot), oi)
                    out.setdefault(k, {})[ot] = (oi, ch)
    return [dict(k=k, c=v.get("CE", (0, 0))[0], cc=v.get("CE", (0, 0))[1],
                 p=v.get("PE", (0, 0))[0], pc=v.get("PE", (0, 0))[1]) for k, v in sorted(out.items())], exp

# ---------- news ----------
def news_score():
    heads = []
    for q in ("Nifty+Sensex+market", "Wall+Street+Asian+markets", "RBI+FII+India+stocks"):
        try:
            u = f"https://news.google.com/rss/search?q={q}+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
            root = ET.fromstring(urllib.request.urlopen(u, timeout=15).read())
            heads += [i.findtext("title", "") for i in root.iter("item")][:12]
        except Exception as e:
            print("news feed skipped:", e)
    s = 0
    for h in heads:
        w = h.lower(); s += sum(x in w for x in POS) - sum(x in w for x in NEG)
    tone = max(-2, min(2, round(s / max(1, len(heads) / 4))))
    return tone, heads[:5]

# ---------- engine (same rules as the web page) ----------
cl = lambda x, a, b: max(a, min(b, x))
def analyze(rows, spot, tone):
    atm = round(spot / STEP) * STEP
    near = [r for r in rows if abs(r["k"] - atm) <= 3 * STEP]
    S = lambda a, f: sum(r[f] for r in a)
    pcr = S(rows, "p") / (S(rows, "c") or 1)
    dP, dC = S(near, "pc"), S(near, "cc")
    flow = (dP - dC) / ((abs(dP) + abs(dC)) or 1)
    sup = max([r for r in rows if r["k"] <= atm], key=lambda r: r["p"])["k"]
    res = max([r for r in rows if r["k"] >= atm], key=lambda r: r["c"])["k"]
    dS, dR = spot - sup, res - spot
    wall = cl((dR - dS) / ((dR + dS) or 1), -1, 1)
    oi = .45 * flow + .3 * cl((pcr - 1) / .4, -1, 1) + .25 * wall
    gap, fii = float(os.environ.get("GAP", 0)), float(os.environ.get("FII", 0))
    ns = .4 * cl(gap / .5, -1, 1) + .3 * fii + .3 * (tone / 2)
    tot = .65 * oi + .35 * ns
    bias = "UP" if tot > .2 else "DOWN" if tot < -.2 else "SIDEWAYS"
    conf = round(40 + abs(tot) * 50) if bias == "SIDEWAYS" else round(cl(45 + abs(tot) * 80, 0, 92))
    return dict(atm=atm, pcr=pcr, sup=sup, res=res, oi=oi, ns=ns, tot=tot, bias=bias, conf=conf, spot=spot)

def strike_line(a):
    if a["bias"] == "SIDEWAYS": return "NO TRADE: OI and news disagree."
    up = a["bias"] == "UP"
    tg = max(a["res"], a["spot"] + STEP) if up else min(a["sup"], a["spot"] - STEP)
    sl = a["sup"] - STEP / 2 if up else a["res"] + STEP / 2
    return f"BUY {int(a['atm'])} {'CE' if up else 'PE'} | target (index) {tg:.0f} | exit if index {'below' if up else 'above'} {sl:.0f}"

def show(a, exp, heads):
    print(f"\n[{dt.datetime.now():%H:%M:%S}] {idx['name']} spot {a['spot']:.0f}  expiry {exp:%d-%b}")
    print(f"CALL: {a['bias']} (confidence {a['conf']}%)  PCR {a['pcr']:.2f}  support {a['sup']:.0f}  resistance {a['res']:.0f}")
    print(strike_line(a))
    for h in heads: print("  news:", h[:100])

def check(lk, a):
    f, up, dn = [], lk["bias"] == "UP", lk["bias"] == "DOWN"; hard = False
    if up and a["spot"] < lk["sup"]: hard = True; f.append(f"spot broke support {lk['sup']:.0f}")
    if dn and a["spot"] > lk["res"]: hard = True; f.append(f"spot broke resistance {lk['res']:.0f}")
    if (up and a["tot"] < -.25) or (dn and a["tot"] > .25): f.append("OI+news score flipped against the view")
    if abs(a["pcr"] - lk["pcr"]) >= .15 and ((up and a["pcr"] < lk["pcr"]) or (dn and a["pcr"] > lk["pcr"])): f.append("PCR moved against the view")
    if up and lk["sup"] - a["sup"] >= 2 * STEP: f.append("support wall shifted down")
    if dn and a["res"] - lk["res"] >= 2 * STEP: f.append("resistance wall shifted up")
    nd = a["ns"] - lk["ns"]
    if (up and nd <= -.5) or (dn and nd >= .5): f.append("news tone turned sharply against the view")
    if hard or len(f) >= 2: return "REVERSAL RISK: reassess or exit", f
    return ("CAUTION: one warning sign", f) if f else ("HOLD the morning view", f)

def snapshot(c):
    spot = spot_price(c); rows, exp = chain(c, spot); tone, heads = news_score()
    return analyze(rows, spot, tone), exp, heads

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "call"
    c = login()
    if mode == "debug":
        s = spot_price(c); print("spot:", s)
        toks, exp = option_tokens(c, s); print("contracts:", len(toks), "expiry", exp, "sample", toks[:2])
        print("raw quote sample:", c.quotes(instrument_tokens=[{"instrument_token": toks[0][2], "exchange_segment": "nse_fo"}], quote_type="all"))
        return
    if mode == "run":
        t = dt.datetime.combine(dt.date.today(), dt.time(9, 20))
        while dt.datetime.now() < t: time.sleep(5)
    a, exp, heads = snapshot(c); show(a, exp, heads)
    json.dump(a, open(LOCK, "w")); print("Locked as the morning view.")
    if mode != "run": return
    end = dt.time(15, 15)
    while dt.datetime.now().time() < end:
        time.sleep(180)
        try:
            n, exp, heads = snapshot(c); v, f = check(a, n)
            print(f"[{dt.datetime.now():%H:%M}] spot {n['spot']:.0f}  {v}" + ("  <- " + "; ".join(f) if f else ""))
        except Exception as e:
            print("re-check failed (session may have expired):", e)

if __name__ == "__main__":
    main()
