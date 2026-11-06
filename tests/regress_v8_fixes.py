"""Behavioral regressions: evidence boundaries, task queues, and audio shapes.

Synthetic WAV cases exercise the real fingerprint; injected histograms exercise
decision logic only and are not presented as real-audio accuracy measurements.
"""
import copy
import json
import sys
import wave
from unittest.mock import patch

import numpy as np
import _paths
import authorcheck as C
import auprint as A
import find_earlier as F

results = []
def check(name, ok):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name)

good = dict(url='https://example.com/original/123', work_id='123',
            account_id='UC_target', input_work_id='BVinput',
            basis='inspected the original credit and its link to this clip',
            evidence_type='creator_credit', evidence_quote='Created by Target; original 123',
            checked_at='2026-11-05T12:00:00Z', verified=True)

def evidence(link):
    return C._evidence('Target', 'UC_target', ['ASMR'], None, None, [],
                       linked_by=link, input_work_id='BVinput')

bad = [('bare string', good['url']), ('empty', None)]
for key, value in [('verified', False), ('verified', 'true'), ('account_id', 'UC_other'),
                   ('input_work_id', 'BVother'), ('evidence_type', 'content_match'),
                   ('checked_at', 'invalid'), ('evidence_quote', ''), ('url', 'not-a-url')]:
    bad.append((f'{key}={value}', {**good, key:value}))
for name, link in bad:
    check('unverified attribution: '+name, evidence(link)['attribution']['verdict'] != 'supports')
    rec = dict(account_id='UC_target', input_work_id='BVinput', linked_by=link)
    check('keep searching: '+name, not C.ladder_may_stop({'x':rec}))
check('explicit reviewed attribution is accepted', evidence(good)['attribution']['verdict']=='supports')
check('same evidence permits stopping', C.ladder_may_stop({'x':dict(
    account_id='UC_target', input_work_id='BVinput', linked_by=good)}))
check('work match alone does not end creator search', not C.ladder_may_stop({'x':{'works':['123']}}))
check('unresolved URL is not provenance', C._evidence('Target','UC_target',[],None,
      'https://example.com/hint',[])['provenance']['verdict']=='unverifiable')
check('resolved account-scoped provenance can support', C._evidence('Target','UC_target',[],None,
      dict(url='https://example.com/hint',resolved=True,account_id='UC_target',
           excerpt='Original by Target'),[])['provenance']['verdict']=='supports')

captured=[]
hits=[dict(platform='youtube', id='abcdefghijk', title='Origami crane tutorial',
           duration=600, search_rank=0, pubdate=0),
      dict(platform='youtube', id='lmnopqrstuv', title='Woodworking chair',
           duration=800, search_rank=1, pubdate=0)]
with patch.object(F,'yt_search',return_value=hits), patch.object(F,'emit',side_effect=captured.append):
    F.main(['--yt','Origami crane tutorial','--duration','600'])
o=captured[-1];q={c['id']:c for c in o['comment_candidates']}
check('both title and structural candidates have comment tasks',set(q)=={'abcdefghijk','lmnopqrstuv'})
check('title candidate also has verification task',o['verification_candidates'][0]['id']=='abcdefghijk')
check('tasks are pending, not implicitly read',all(c['status']=='pending' and not c['evidence'] for c in q.values()))
check('YouTube reader stays on YouTube',all(c['platform']=='youtube' and 'comments.py' not in c['read_with'] for c in q.values()))
check('task has blocked fallback',all(c['on_blocked'] for c in q.values()))
check('no content claim from titles',o['content_match_state']=='not_checked')
check('query mode does not invent a video ID',o['reference']['id'] is None)
check('comment task has priority',o['next_action']=='inspect_comments_and_credits')
with patch.object(F,'yt_search',return_value=[]), patch.object(F,'emit',side_effect=captured.append):
    F.main(['--yt','Origami crane tutorial'])
check('empty results widen search',captured[-1]['next_action']=='widen_query_set')

def controlled(hist):
    ha,hb={},{}
    for lag,count in hist.items():
        for j in range(count):
            key=(lag,j,1);t=j%120
            ha[key]=[t];hb[key]=[t+lag]
    x=np.random.default_rng(2026).normal(0,.12,A.SR*12)
    with patch.object(A,'to_wav',side_effect=lambda p:(p,False)), \
         patch.object(A,'read_wav',return_value=x), \
         patch.object(A,'fingerprints',return_value=[]), \
         patch.object(A,'hash_index',side_effect=[ha,hb]):
        return A.match('controlled-a','controlled-b')

flat={i:100 for i in range(50)}
shallow={i:140 for i in range(51)};shallow[5]+=100;shallow[45]+=100
deep={i:40 for i in range(51)};deep[5]+=1000;deep[45]+=1000
for label,h in [('flat',flat),('shallow',shallow)]:
    r=controlled(h)
    check(label+' remains undecided',r['verdict']=='insufficient_evidence' and r['proves']=='')
    check(label+' exports raw evidence',bool(r['raw_alignments']) and bool(r['alignment_interval_s']))
    if label=='shallow':check('shallow rival is retained',r['rival_lag_votes']==1080)
r=controlled(deep)
check('deep double peak stays ambiguous',r['verdict']=='ambiguous_alignment')
check('reported offset is an enumerated alignment',any(abs(p['lag_s']-r['modal_lag_s'])<.011 for p in r['alignments']))
check('reported peak counts are exact',all(v==A._window_sum(deep,c,A.CLUSTER_RADIUS)
      for c,v in A.enumerate_alignments(deep,A.CLUSTER_RADIUS,A.MIN_ALIGNMENT_VOTES)))

def wav(name,x):
    p=_paths.out(name)
    with wave.open(p,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(A.SR)
        f.writeframes((np.clip(x,-1,1)*30000).astype('<i2').tobytes())
    return p
def bands(seed):
    x=np.random.default_rng(seed).normal(0,.12,A.SR*12)
    z=np.fft.rfft(x);freq=np.fft.rfftfreq(len(x),1/A.SR)
    z[~(((freq>=100)&(freq<=500))|((freq>=2800)&(freq<=3200)))]=0
    return np.fft.irfft(z,n=len(x))
x=bands(2026);pa=wav('multi_band_a.wav',x);pb=wav('multi_band_b.wav',bands(2027))
r=A.match(pa,pa)
check('two broad bands match themselves',r['verdict']=='same_audio_segment')
check('spectral profiles reach the result',r['spectral_profile_a']['narrow_peak_share']<.55)
r=A.match(pa,pb)
check('independent two-band noise is not a positive',r['verdict'] not in ('same_audio_segment','ambiguous_alignment'))
t=np.arange(A.SR*12)/A.SR
chord=sum(np.sin(2*np.pi*f*t) for f in (220,440,660,880,1320,1760))/6
check('six pure tones remain unsupported',A._tonal_ratio(chord)>A.TONAL_CUTOFF)
check('single pure tone remains unsupported',A._tonal_ratio(np.sin(2*np.pi*440*t))>A.TONAL_CUTOFF)
print(f'{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
