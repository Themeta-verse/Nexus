#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
import github_live_loop as g
from beyond_engine import recovery
from adaptive_engine import outcome_learning
from godtier_governance import classify_action

# Real authenticated read test; this repository was discovered from the live account and is treated provisionally.
real=g.run('Themeta-verse/Nexus')
assert real['verification']['github_read_succeeded'] is True
assert real['verification']['writes_performed'] is False
assert real['snapshot']['repository']=='Themeta-verse/Nexus'
assert real['comparison']['status'] in {'baseline','compared'}
assert real['health']['evidence_boundary']

# Missing repository: safe failure, no invented state.
original=g.gh_api
def missing(path, params=None):
    raise RuntimeError('GitHub request failed for repos/missing/repo: HTTP 404 Not Found')
g.gh_api=missing
try:
    try:
        g.run('missing/repo')
    except RuntimeError as e:
        assert '404' in str(e)
finally:
    g.gh_api=original

# Partial local recovery is explicit.
r=recovery('RESEARCHING',['snapshot','analysis'],['recommendation'],['user confirmation'])
assert r['next_action']=='recommendation'

# Learning is explicit, not silent governance mutation.
lesson=outcome_learning({'expected_result':'no open work','decision':'baseline'}, 'no open work')
assert lesson['alignment'] is True

# Consequential writes remain gated.
assert classify_action('merge')['approval_required'] is True
assert classify_action('read')['approval_required'] is False
print(json.dumps({'status':'passed','real_repository':'Themeta-verse/Nexus'
,'safe_failure':'404 preserved without invented state','recovery':'passed','learning':'passed','approval_boundary':'passed'},indent=2))
