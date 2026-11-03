"""Separate raw model presence, per-label warning events and grouped speech truth."""
import argparse
import json
from pathlib import Path
from summarize_vision_stress import summarize

FAMILIES={'possible_puddle':'wet_surface','pothole':'damaged_pavement',
          'damaged_pavement':'damaged_pavement','stairs':'stairs'}
KEYS={'wet_surface':['visible_puddle'],'damaged_pavement':['visible_pavement_damage','visible_pothole'],
      'stairs':['stairs_visible']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['catalog','before','after','out']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();catalog=json.loads(a.catalog.read_text(encoding='utf8'))
    reports={version:json.loads(path.read_text(encoding='utf8')) for version,path in [('before',a.before),('after',a.after)]}
    scored={version:summarize(catalog,report) for version,report in reports.items()}
    result={'basis':'Per-photo event presence, not mAP or field accuracy. Raw presence includes context-only model outputs. Merging speech families is not a detector accuracy gain.',
            'photo_event_categories':{},'speech_families':{},'raw_detector_presence':{},'negative_groups':{},
            'all_label_outcomes_unchanged':scored['before']['categories']==scored['after']['categories']}
    for label in FAMILIES:
        result['photo_event_categories'][label]={version:{k:s['categories'][label][k] for k in ['tp','fn','fp','tn']}
                                                for version,s in scored.items()}
    for version,report in reports.items():
        cases={c['source_file']:c for c in report['cases'].values()}
        for family,keys in KEYS.items():
            counts=dict(tp=0,fn=0,fp=0,tn=0,unknown=0)
            for item in catalog['items']:
                truths=[item['ground_truth'].get(key) for key in keys]
                truth=True if True in truths else False if all(t is False for t in truths) else None
                emitted=any(FAMILIES[e['object_class']]==family for e in cases[item['file']]['warnings'])
                outcome='unknown' if truth is None else ('tp' if emitted else 'fn') if truth else ('fp' if emitted else 'tn')
                counts[outcome]+=1
            result['speech_families'].setdefault(family,{})[version]=counts
        result['raw_detector_presence'][version]={label:sum(any(d['label']==label for d in c['raw_detections'])
                                                                          for c in cases.values()) for label in FAMILIES}
    for group in sorted({g for i in catalog['items'] for g in i.get('coverage_groups',[])}):
        rows=[c for c in scored['after']['cases'] if group in c['groups']]
        result['negative_groups'][group]={'cases':len(rows),'cases_with_false_alerts':sum(bool(c['false_alerts']) for c in rows)}
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'unchanged':result['all_label_outcomes_unchanged'],'raw_presence':result['raw_detector_presence']},ensure_ascii=False))


if __name__=='__main__':main()
