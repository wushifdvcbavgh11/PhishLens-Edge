"""Bounded, display-only projection of the actual visual result.

Never promotes a nearest neighbour to a confirmed brand, and never decides
whether to block. Similarities are uncalibrated scores, not probabilities.
"""
from copy import deepcopy
from urllib.parse import urlparse


def summarize_brand_evidence(original, page_url, context_valid):
    evidence = original.get('visual_evidence') or {}
    skipped = 'WHITELIST_MATCH' in (original.get('reason_codes') or [])
    completed = original.get('analysis_status') == 'completed' and not skipped
    hit = evidence.get('hit') or {}
    brand = original.get('brand')
    domains = [d.strip() for d in (original.get('official_domain') or '').split(',') if d.strip()]
    try:
        host = (urlparse(page_url).hostname or '').lower().rstrip('.')
    except ValueError:
        host = ''
    check = 'not_evaluated'
    if completed and context_valid and host and brand and domains:
        check = 'match' if any(host == d.lower().rstrip('.') or
                              host.endswith('.' + d.lower().rstrip('.')) for d in domains) else 'mismatch'
    candidates = {}
    regions = []
    # Failed/skipped results must not revive stale visual evidence.
    if completed:
        for region in evidence.get('regions', [])[:10]:
            item = {key: deepcopy(region.get(key)) for key in ('box', 'source', 'rejection', 'brand_margin')}
            item['candidates'] = []
            for candidate in region.get('candidates', [])[:3]:
                compact = {key: deepcopy(candidate.get(key)) for key in
                           ('name', 'similarity', 'domains', 'reference')}
                item['candidates'].append(compact)
                name = compact['name']
                score = compact['similarity']
                if not name or not isinstance(score, (int, float)):
                    continue
                if name not in candidates or score > candidates[name]['similarity']:
                    candidates[name] = {**compact, 'box': deepcopy(item['box']),
                                        'source': item['source'], 'rejection': item['rejection']}
            regions.append(item)
    return {
        'brand': brand if completed else None,
        'similarity': hit.get('similarity') if completed and brand and hit.get('name') == brand else None,
        'official_domains': domains if completed and brand else [],
        'current_domain': host or None,
        'domain_check': check,
        'domain_reason': {'match': 'HOST_MATCHES_CONFIGURED_BRAND_DOMAIN',
                          'mismatch': 'HOST_OUTSIDE_CONFIGURED_BRAND_DOMAINS',
                          'not_evaluated': 'BRAND_OR_CONTEXT_NOT_CONFIRMED'}[check],
        'candidates': sorted(candidates.values(), key=lambda c: c['similarity'], reverse=True)[:3],
        'regions': regions,
        'image_size': deepcopy(evidence.get('image_size')) if completed else None,
        'reference_version': evidence.get('reference_version') if completed else None,
        'threshold': evidence.get('threshold') if completed else None,
        'brand_margin_required': evidence.get('brand_margin_required') if completed else None,
        'score_kind': 'similarity_not_probability',
    }
