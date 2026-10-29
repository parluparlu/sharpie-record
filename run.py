"""Scheduled record job. Opens the published board in a headless browser as the single writer,
lets it lock plays before kickoff and grade finished games, then saves the shared ledger.json."""
import json, os, sys, hashlib, datetime

KEYS = ['picks', 'shadow', 'clvl', 'voided', 'seen']
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ledger.json')

def load_old(path=LEDGER):
    try:
        with open(path) as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}

def init_script(old):
    seed = {('wsb.' + k): json.dumps(old.get(k, [] if k in ('picks', 'voided') else {})) for k in KEYS}
    seed.update({'wsb.welcomed': 'true', 'wsb.cal2': '1', 'wsb.cal3': '1', 'wsb.cal4': '1', 'wsb.cal5': '1'})
    return "(()=>{try{const s=%s;for(const k in s)localStorage.setItem(k,s[k])}catch(e){}})()" % json.dumps(seed)

def wait_done(page, timeout=240000):
    page.wait_for_function("()=>typeof S!=='undefined'&&S.lastRefresh&&!S.busy", timeout=timeout)
    page.wait_for_timeout(4000)

def extract(page):
    out = page.evaluate("""()=>{const o={};for(const k of %s){try{o[k]=JSON.parse(localStorage.getItem('wsb.'+k))}catch(e){o[k]=null}}
      o._ver=MODEL_VER;o._errors=S.errors||[];return o}""" % json.dumps(KEYS))
    return out

def digest(d):
    return hashlib.sha256(json.dumps({k: d.get(k) for k in KEYS}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def merge(old, new, now=None):
    """Returns the new ledger dict, or raises if the run looks unsafe."""
    errs = new.get('_errors') or []
    if sum(1 for e in errs if 'failed to load' in e) >= 2:
        raise RuntimeError('both data feeds failed: ' + '; '.join(errs))
    picks = new.get('picks') or []
    if not isinstance(picks, list):
        raise RuntimeError('picks missing')
    cutoff = (now or datetime.datetime.utcnow()) - datetime.timedelta(days=1)
    have = {p.get('key') for p in picks}
    for p in old.get('picks', []):
        try:
            d = datetime.datetime.fromisoformat(str(p.get('date')).replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            continue
        if d < cutoff and p.get('key') not in have:
            raise RuntimeError('a past play disappeared: %s' % p.get('key'))
    led = {'updated': (now or datetime.datetime.utcnow()).strftime('%Y-%m-%dT%H:%M:%SZ'), 'ver': new.get('_ver')}
    for k in KEYS:
        led[k] = new.get(k) if new.get(k) is not None else ([] if k in ('picks', 'voided') else {})
    return led

def save(led, old, path=LEDGER):
    if old and digest(old) == digest(led):
        print('no change'); return False
    with open(path, 'w') as f:
        json.dump(led, f, separators=(',', ':'), sort_keys=True)
    print('ledger written: %d plays, %d games' % (len(led['picks']), len(led['shadow']))); return True

def main():
    from playwright.sync_api import sync_playwright
    site = os.environ['SITE_URL'].rstrip('/')
    old = load_old()
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={'width': 1280, 'height': 900})
        ctx.add_init_script(init_script(old))
        page = ctx.new_page()
        page.goto(site + '/#job', wait_until='load')
        wait_done(page)
        new = extract(page)
        b.close()
    save(merge(old, new), old)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('FAILED:', e); sys.exit(1)
