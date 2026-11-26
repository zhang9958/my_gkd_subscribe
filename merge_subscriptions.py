"""Extend a pinned GKD baseline with independent, app-scoped ad rules."""

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

import json5

AD_NAMES = ('开屏广告', '全屏广告', '局部广告', '分段广告')
METADATA = {'name', 'desc', 'snapshotUrls', 'excludeSnapshotUrls', 'exampleUrls'}
REFERENCES = ('preKeys', 'actionCdKey', 'actionMaximumKey')
SCHEDULING = {'actionCd', 'actionDelay', 'actionMaximum', 'fastQuery',
              'forcedTime', 'matchDelay', 'matchTime', 'order', 'priorityTime',
              'priorityActionMaximum', 'resetMatch'}
SENSITIVE_NAMES = ('登录', '授权', '支付', '付款', '免密', '协议', '权限', '下单', '购买')


def array(value):
    return [] if value is None else value if isinstance(value, list) else [value]


def rules(group):
    return [dict(matches=rule) if isinstance(rule, str) else rule
            for rule in array(group.get('rules'))]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))


def is_ad(group):
    return group['name'].startswith(AD_NAMES)


def is_splash(group):
    return group['name'].startswith('开屏广告')


def sensitive_action(group):
    if any(word in group['name'] for word in SENSITIVE_NAMES):
        return True
    for rule in rules(group):
        for selector in array(rule.get('matches')) + array(rule.get('anyMatches')):
            for _, value in re.findall(r"\b(?:text|desc)\s*=\s*([\"'])([^\"']+)\1", selector):
                if value.startswith(('同意', '允许', '授权', '登录', '支付', '付款')):
                    return True
                if value in {'立即支付', '直接支付', '确认支付', '确认付款',
                             '立即购买', '立即下单', '提交订单'}:
                    return True
    return False


def linked(group):
    return bool(group.get('scopeKeys')) or any(
        key in obj for obj in [group] + rules(group) for key in REFERENCES)


def runtime_rule(group, rule, keep_key=False):
    excluded = METADATA | SCHEDULING | {'key', 'enable', 'scopeKeys', 'rules'}
    effective = {k: v for k, v in group.items() if k not in excluded}
    effective.update({k: v for k, v in rule.items()
                      if k not in METADATA | SCHEDULING and (keep_key or k != 'key')})
    effective.setdefault('action', 'click')
    for key in ('matches', 'anyMatches', 'excludeMatches',
                'activityIds', 'excludeActivityIds'):
        if key in effective:
            effective[key] = array(effective[key])
    # Selector order is significant; do not sort or rewrite selector text.
    return effective


def components(groups):
    """Keep scope-connected groups atomic, including reverse dependencies."""
    by_key = {g['key']: g for g in groups}
    if len(by_key) != len(groups):
        raise ValueError('Duplicate group keys in an upstream app')
    neighbors = {k: set() for k in by_key}
    for group in groups:
        for key in array(group.get('scopeKeys')):
            if key in by_key:
                neighbors[group['key']].add(key)
                neighbors[key].add(group['key'])
    remaining = set(by_key)
    for group in groups:
        if group['key'] not in remaining:
            continue
        todo, found = [group['key']], set()
        while todo:
            key = todo.pop()
            if key not in found:
                found.add(key)
                todo.extend(neighbors[key] - found)
        remaining -= found
        yield [g for g in groups if g['key'] in found]


def unit_signature(unit):
    entries = []
    for group in unit:
        entries.append((group, {'rules': [runtime_rule(group, r, True)
                                         for r in rules(group)]}))
    entries.sort(key=lambda item: canonical(item[1]))
    indices = {group['key']: i for i, (group, _) in enumerate(entries)}
    for group, value in entries:
        value['scopeKeys'] = [indices.get(k, 'missing:' + str(k))
                              for k in array(group.get('scopeKeys'))]
    return canonical([value for _, value in entries])


def reference_errors(unit):
    by_key = {g['key']: g for g in unit}
    errors = []
    for group in unit:
        scopes = array(group.get('scopeKeys'))
        missing = [k for k in scopes if k not in by_key]
        if missing:
            errors.append(f"group {group['key']}: missing scope keys {missing}")
        available = {r['key'] for g in [group] + [by_key[k] for k in scopes
                                                if k in by_key]
                     for r in rules(g) if 'key' in r}
        own_keys = [r['key'] for r in rules(group) if 'key' in r]
        if len(own_keys) != len(set(own_keys)):
            errors.append(f"group {group['key']}: duplicate rule keys")
        for obj in [group] + rules(group):
            for prop in REFERENCES:
                for key in array(obj.get(prop)):
                    if key not in available:
                        errors.append(f"group {group['key']}: {prop} -> {key}")
    return errors


def stable_key(source, app_id, original_key):
    identity = canonical([source, app_id, original_key]).encode()
    return (int.from_bytes(hashlib.sha256(identity).digest()[:4], 'big')
            & 0x3fffffff) | 0x40000000


def counts(subscription):
    groups = [g for app in subscription['apps'] for g in app['groups']]
    return {'apps': len(subscription['apps']), 'app_groups': len(groups),
            'app_rules': sum(len(rules(g)) for g in groups),
            'global_groups': len(subscription.get('globalGroups', [])),
            'enabled_app_groups': sum(g.get('enable', True) for g in groups)}


def integrate(base, supplements):
    result = copy.deepcopy(base)
    result['name'] = '国内 App 广告订阅 · 精选多源集成'
    result.pop('updateUrl', None)
    result.pop('checkUpdateUrl', None)
    apps = {app['id']: app for app in result['apps']}
    report = {'before': counts(base), 'sources': []}
    for source, document in supplements:
        stats = Counter()
        skipped = []
        for upstream_app in document.get('apps', []):
            app_id = upstream_app['id']
            app = apps.get(app_id, {'id': app_id,
                                   'name': upstream_app.get('name', app_id),
                                   'groups': []})
            existing_units = {unit_signature(u) for u in components(app['groups'])}
            independent = {canonical(runtime_rule(g, r)) for g in app['groups']
                           if not linked(g) for r in rules(g)}
            used_keys = {g['key'] for g in app['groups']}
            for unit in components(upstream_app.get('groups', [])):
                ad_count = sum(is_ad(g) for g in unit)
                if not ad_count:
                    stats['non_ad_groups_excluded'] += len(unit)
                    continue
                problem = None
                if ad_count != len(unit):
                    problem = 'ad group depends on a non-ad group'
                elif any(sensitive_action(g) for g in unit):
                    problem = 'account, permission or payment operation excluded'
                elif any(is_splash(g) and g.get('enable') is False for g in unit):
                    problem = 'splash group is disabled by upstream'
                elif any(not rules(g) for g in unit):
                    problem = 'empty rule group'
                elif any(is_splash(g) for g in unit) and not all(is_splash(g) for g in unit):
                    problem = 'splash group depends on a disabled non-splash group'
                else:
                    errors = reference_errors(unit)
                    if errors:
                        problem = '; '.join(errors)
                if problem:
                    skipped.append({'app': app_id, 'groups': [g['key'] for g in unit],
                                    'reason': problem})
                    stats['invalid_or_mixed_groups_excluded'] += len(unit)
                    continue
                if unit_signature(unit) in existing_units:
                    stats['duplicate_groups_removed'] += len(unit)
                    continue
                new_unit = copy.deepcopy(unit)
                if len(new_unit) == 1 and not linked(new_unit[0]):
                    group = new_unit[0]
                    retained = []
                    for rule in rules(group):
                        signature = canonical(runtime_rule(group, rule))
                        if signature in independent:
                            stats['duplicate_rules_removed'] += 1
                        else:
                            retained.append(rule)
                            independent.add(signature)
                    if not retained:
                        stats['duplicate_groups_removed'] += 1
                        continue
                    group['rules'] = retained
                # Do not prune steps from a stateful action chain.
                key_map = {g['key']: stable_key(source['id'], app_id, g['key'])
                           for g in new_unit}
                if len(set(key_map.values())) != len(key_map) or used_keys.intersection(key_map.values()):
                    raise ValueError(f"Group-key collision: {source['id']} / {app_id}")
                for group in new_unit:
                    group['key'] = key_map[group['key']]
                    if 'scopeKeys' in group:
                        group['scopeKeys'] = [key_map[k] for k in array(group['scopeKeys'])]
                    group['enable'] = is_splash(group)
                    origin = f"来源：{source['name']} ({source['repository']})"
                    group['desc'] = (group.get('desc', '') + '\n' + origin).strip()
                    used_keys.add(group['key'])
                    stats['groups_added'] += 1
                    stats['rules_added'] += len(rules(group))
                app['groups'].extend(new_unit)
                existing_units.add(unit_signature(new_unit))
            if app_id not in apps and app['groups']:
                result['apps'].append(app)
                apps[app_id] = app
                stats['apps_added'] += 1
        # Baseline global matching is already shared across all apps. Extra
        # global groups would repeat the same scans and are deliberately absent.
        stats['supplemental_global_groups_excluded'] = len(document.get('globalGroups', []))
        report['sources'].append({'id': source['id'], 'name': source['name'],
                                  'version': document.get('version'),
                                  'url': source['url'], 'license': source['license'],
                                  'stats': dict(stats), 'skipped': skipped})
    report['after'] = counts(result)
    return result, report


def load(url):
    request = Request(url, headers={'User-Agent': 'my-gkd-subscribe-merger'})
    with urlopen(request, timeout=40) as response:
        raw = response.read()
    return json5.loads(raw.decode('utf-8')), raw


def next_version(result, previous, base_version):
    comparable = lambda value: {k: v for k, v in value.items() if k != 'version'}
    if previous is not None and comparable(previous) == comparable(result):
        return previous['version']
    return max(base_version, previous['version'] if previous else 0) + 1


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=root / 'sources.json')
    parser.add_argument('--output', type=Path, default=root / 'gkd-merged.json5')
    parser.add_argument('--report', type=Path, default=root / 'integration-report.json')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    base, base_raw = load(config['base_url'])
    inputs, provenance = [], []
    for source in config['sources']:
        document, raw = load(source['url'])
        inputs.append((source, document))
        provenance.append({'id': source['id'], 'sha256': hashlib.sha256(raw).hexdigest(),
                           'version': document.get('version')})
    result, report = integrate(base, inputs)
    previous = json5.loads(args.output.read_text(encoding='utf-8')) if args.output.exists() else None
    result['version'] = next_version(result, previous, base['version'])
    report.update(version=result['version'], provenance=provenance)
    comments = [line for line in base_raw.decode('utf-8').splitlines()
                if line.lstrip().startswith('//')]
    comments.append('// Additional app-specific advertising rules; no extra global scans.')
    for source in config['sources']:
        comments.append(f"// {source['name']}: {source['repository']} ; {source['license']}: {source['license_url']}")
    args.output.write_text('\n'.join(comments) + '\n' + canonical(result) + '\n', encoding='utf-8')
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'version': result['version'], 'before': report['before'],
                      'after': report['after'], 'sources': report['sources']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
