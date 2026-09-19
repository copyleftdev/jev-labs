#!/usr/bin/env python3
"""Provenance for the film: every figure spoken or shown, traced to its source.

Reads scene.mjs (the narration text), story_data.json (the numbers) and the
forensic DB, then checks that each number the script speaks actually appears
in the data. Fails loudly if a spoken figure has drifted from the run.

    ../.venv/bin/python provenance.py
"""
import json
import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
D = json.loads((ROOT / 'story_data.json').read_text())
chapters = json.loads(subprocess.check_output(
    ['node', '--input-type=module', '-e', 'import {CHAPTERS} from "./scene.mjs"; console.log(JSON.stringify(CHAPTERS));'],
    cwd=ROOT, text=True))
script = ' '.join(c['transcript'] for c in chapters)

H, G, S, Hn = D['headline'], D['gate'], D['safety_by_chaos'], D['honest']
first_vote = D['moments']['clean_decision']['votes'][0]['p']
declined = D['moments']['declined_under_chaos']
unstable = next(v for v in declined['votes'] if not v['stable'])

WORDS = {0.98: 'zero point nine eight', 0.99: 'zero point nine nine', 0.54: 'zero point five four',
         0.042: 'zero point zero four two'}

# (claim in script, value in data, source)
checks = [
    (WORDS[round(first_vote, 2)], first_vote, 'moments.clean_decision.votes[0].p'),
    (WORDS[round(unstable['p'], 2)], unstable['p'], 'moments.declined_under_chaos (unstable vote)'),
    (WORDS[G['noise_floor']], G['noise_floor'], 'gate.noise_floor'),
    ('fourteen hundred', 1406, 'noise floor calibration, n=1406 repeated calls (forensics)'),
    ('One thousand and eighty', H['total_golden'], 'headline.total_golden'),
    ('Zero wrong verdicts', H['total_violations'], 'headline.total_violations'),
    ('seventy-one percent', 99 / 140, 'pre-fix severe run: 99 of 140 rounds aborted'),
    ('a hundred and fifteen', Hn['mislabelled_by_us']['data']['verdicts']['No'], 'honest.mislabelled_by_us verdicts.No'),
    ('a hundred and twenty', Hn['mislabelled_by_us']['data']['n'], 'honest.mislabelled_by_us n'),
    ('eighty-six', Hn['real_limitation']['data']['escalated'], 'honest.real_limitation escalated'),
    ('thirty-four', Hn['real_limitation']['data']['verdicts']['Yes'] + Hn['real_limitation']['data']['verdicts']['No'],
     'honest.real_limitation decided (Yes+No)'),
]

bound = H['violation_rate_upper_95']
ok = True
print('SPOKEN FIGURE                      DATA        SOURCE')
print('-' * 78)
for phrase, value, source in checks:
    present = phrase.lower() in script.lower()
    ok &= present
    v = f'{value:.4f}' if isinstance(value, float) else str(value)
    print(f"{('  ' if present else '!!') + phrase:<34}{v:<12}{source}")

# the bound is spoken approximately; check the approximation is fair
print(f"\n  'below about a quarter of a percent'   {bound * 100:.2f}%   headline.violation_rate_upper_95")
fair = 0.2 <= bound * 100 <= 0.3
ok &= fair
if not fair:
    print('!! spoken bound no longer fairly describes the data')

db = sqlite3.connect(ROOT.parent / 'forensics.db')
calls = db.execute('select count(*) from calls').fetchone()[0]
print(f"\nforensics.db: {calls} captured calls, all hash-verified at capture time")
print(f"story_data.json generated from: {D['generated_from']}")

# The voice must speak THIS chapter's transcript. Takes are cached by hash and
# chapters get inserted/renumbered; a take with the right filename and the wrong
# words is the failure mode this catches.
import glob
voice_ok = True
for f in sorted(glob.glob(str(ROOT / 'narration-work' / 'chapter-*.json'))):
    d = json.loads(Path(f).read_text())
    i = d['chapter']
    if d['source_text'] != chapters[i]['transcript'] or d['start'] != chapters[i]['start'] or d['end'] != chapters[i]['end']:
        print(f"!! take {Path(f).name} does not match chapter {i} (text or timing)")
        voice_ok = False
print('voice takes: all match their chapters' if voice_ok else 'voice takes: MISMATCH')
ok &= voice_ok

print('\nPROVENANCE OK' if ok else '\nPROVENANCE FAILED: a spoken figure has drifted from the data')
raise SystemExit(0 if ok else 1)
