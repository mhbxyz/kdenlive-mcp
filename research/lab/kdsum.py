"""Summarize a .kdenlive timeline: bin, tracks (playlist entries/blanks), transitions, filters, guides."""
import sys, xml.etree.ElementTree as ET
r = ET.parse(sys.argv[1]).getroot()
byid = {e.get('id'): e for e in r}
P = lambda e, n: next((p.text for p in e.findall('property') if p.get('name') == n), None)
mb = byid['main_bin']
print("version", P(mb, 'kdenlive:docproperties.version'), P(mb, 'kdenlive:docproperties.kdenliveversion'), "profile", r.find('profile').get('description'))
print("bin:", [(e.get('producer'), P(byid[e.get('producer')], 'resource') if e.get('producer') in byid else None) for e in mb.findall('entry')])
seq = [e for e in r if e.tag == 'tractor' and P(e, 'kdenlive:uuid')]
for s in seq:
    print("SEQUENCE", s.get('id'), P(s, 'kdenlive:clipname'), "guides:", (P(s, 'kdenlive:sequenceproperties.guides') or '')[:200])
    for t in s.findall('track'):
        tr = byid.get(t.get('producer'))
        if tr is None or tr.tag != 'tractor': print("  track", t.get('producer')); continue
        kind = 'A' if P(tr, 'kdenlive:audio_track') else 'V'
        for sub in tr.findall('track'):
            pl = byid[sub.get('producer')]
            items = [('blank', b.get('length')) if b.tag == 'blank' else (b.get('producer'), b.get('in'), b.get('out'), [P(f,'mlt_service') for f in b.findall('filter')]) for b in pl if b.tag in ('entry', 'blank')]
            if items: print(f"  {kind} {tr.get('id')}/{pl.get('id')}:", items)
        print(f"  {kind} {tr.get('id')} filters:", [P(f,'mlt_service') for f in tr.findall('filter') if not P(f,'internal_added')])
    for tn in s.findall('transition'):
        if not P(tn, 'internal_added'): print("  TRANSITION", P(tn,'mlt_service'), P(tn,'kdenlive_id'), tn.get('in'), tn.get('out'), P(tn,'a_track'), P(tn,'b_track'))
