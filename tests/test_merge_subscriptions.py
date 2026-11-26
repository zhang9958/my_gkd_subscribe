import copy
import unittest

from merge_subscriptions import integrate, next_version


def source(identifier):
    return {'id': identifier, 'name': identifier,
            'repository': 'https://example.invalid/' + identifier,
            'url': 'https://example.invalid/' + identifier + '.json5',
            'license': 'MIT'}


def baseline(groups=None):
    return {'id': 2026112601, 'name': 'baseline', 'version': 1,
            'categories': [{'key': 0, 'name': '开屏广告', 'enable': True}],
            'globalGroups': [{'key': 0, 'name': '开屏广告-全局',
                              'enable': True, 'rules': '[text="跳过"]'}],
            'apps': [{'id': 'com.example.app', 'name': 'Example',
                      'groups': groups or []}]}


def document(groups, app_id='com.example.app'):
    return {'version': 9, 'apps': [{'id': app_id, 'name': 'Example',
                                  'groups': groups}]}


def chain(first=0, second=9):
    return [
        {'key': first, 'name': '开屏广告-第一步', 'scopeKeys': [second],
         'rules': [{'key': 10, 'preKeys': [20],
                    'matches': '[vid="close"]'}]},
        {'key': second, 'name': '开屏广告-前置步骤',
         'rules': [{'key': 20, 'matches': '[vid="skip"]'}]},
    ]


class MergeContracts(unittest.TestCase):
    def test_duplicate_rule_removed_but_other_activity_retained(self):
        base = baseline([{'key': 41, 'name': '开屏广告', 'fastQuery': True,
                          'activityIds': ['SplashActivity'],
                          'rules': {'matches': '[vid="skip"]'}}])
        extra = document([{'key': 0, 'name': '开屏广告', 'fastQuery': True,
                           'activityIds': 'SplashActivity',
                           'rules': [{'matches': '[vid="skip"]'},
                                     {'matches': '[vid="skip"]',
                                      'activityIds': 'OtherActivity'}]}])
        merged, report = integrate(base, [(source('a'), extra)])
        added = merged['apps'][0]['groups'][1]
        self.assertEqual(added['rules'], [{'matches': '[vid="skip"]',
                                          'activityIds': 'OtherActivity'}])
        self.assertEqual(report['sources'][0]['stats']['duplicate_rules_removed'], 1)
        self.assertEqual(merged['apps'][0]['groups'][0], base['apps'][0]['groups'][0])

    def test_reference_chain_is_not_partially_pruned(self):
        base = baseline([{'key': 41, 'name': '开屏广告',
                          'rules': '[vid="skip"]'}])
        extra = document([{'key': 0, 'name': '开屏广告',
                           'rules': [{'key': 0, 'matches': '[vid="skip"]'},
                                     {'key': 1, 'preKeys': [0],
                                      'matches': '[vid="close"]'}]}])
        merged, _ = integrate(base, [(source('a'), extra)])
        actions = merged['apps'][0]['groups'][1]['rules']
        self.assertEqual([r['key'] for r in actions], [0, 1])
        self.assertEqual(actions[1]['preKeys'], [0])

    def test_cross_group_references_are_remapped_together(self):
        merged, _ = integrate(baseline(), [(source('a'), document(chain()))])
        first, second = merged['apps'][0]['groups']
        self.assertEqual(first['scopeKeys'], [second['key']])
        self.assertEqual(first['rules'][0]['preKeys'], [second['rules'][0]['key']])
        self.assertTrue(first['enable'] and second['enable'])

    def test_equivalent_scope_graph_is_deduplicated_across_sources(self):
        merged, report = integrate(baseline(), [
            (source('a'), document(chain(0, 9))),
            (source('b'), document(chain(17, 32))),
        ])
        self.assertEqual(len(merged['apps'][0]['groups']), 2)
        self.assertEqual(report['sources'][1]['stats']['duplicate_groups_removed'], 2)

    def test_ad_chain_requiring_authorization_is_not_imported(self):
        groups = [
            {'key': 0, 'name': '开屏广告', 'scopeKeys': [7],
             'rules': {'key': 0, 'preKeys': [1], 'matches': '[vid="skip"]'}},
            {'key': 7, 'name': '功能类-自动授权',
             'rules': {'key': 1, 'matches': '[text="授权"]'}},
        ]
        merged, report = integrate(baseline(), [(source('a'), document(groups))])
        self.assertEqual(merged['apps'][0]['groups'], [])
        self.assertEqual(report['sources'][0]['stats']['invalid_or_mixed_groups_excluded'], 2)

    def test_only_splash_defaults_active_and_no_extra_global_scans(self):
        extra = document([
            {'key': 0, 'name': '开屏广告（定制）', 'rules': '[vid="skip"]'},
            {'key': 1, 'name': '全屏广告', 'enable': True,
             'rules': '[vid="close"]'},
            {'key': 2, 'name': '功能类-登录授权', 'enable': True,
             'rules': '[text="授权"]'},
        ])
        extra['globalGroups'] = [{'key': 0, 'name': '开屏广告',
                                  'rules': '[text="跳过"]'}]
        base = baseline()
        merged, _ = integrate(base, [(source('a'), extra)])
        self.assertEqual([(g['name'], g['enable']) for g in merged['apps'][0]['groups']],
                         [('开屏广告（定制）', True), ('全屏广告', False)])
        self.assertEqual(merged['globalGroups'], base['globalGroups'])

    def test_upstream_disabled_splash_rule_stays_out(self):
        extra = document([{'key': 0, 'name': '开屏广告', 'enable': False,
                           'rules': '[vid="skip"]'}])
        merged, report = integrate(baseline(), [(source('a'), extra)])
        self.assertEqual(merged['apps'][0]['groups'], [])
        self.assertEqual(report['sources'][0]['stats']['invalid_or_mixed_groups_excluded'], 1)

    def test_group_keys_do_not_depend_on_source_order(self):
        a = (source('a'), document([{'key': 0, 'name': '开屏广告',
                                    'rules': '[vid="skip_a"]'}]))
        b = (source('b'), document([{'key': 0, 'name': '开屏广告',
                                    'rules': '[vid="skip_b"]'}], 'com.other.app'))
        one, _ = integrate(baseline(), [a])
        two, _ = integrate(baseline(), [b, a])
        key_one = one['apps'][0]['groups'][0]['key']
        key_two = next(app for app in two['apps'] if app['id'] == 'com.example.app')['groups'][0]['key']
        self.assertEqual(key_one, key_two)

    def test_missing_scope_is_not_published_as_a_broken_rule(self):
        extra = document([{'key': 0, 'name': '开屏广告', 'scopeKeys': [999],
                           'rules': '[vid="skip"]'}])
        merged, report = integrate(baseline(), [(source('a'), extra)])
        self.assertEqual(merged['apps'][0]['groups'], [])
        self.assertTrue(report['sources'][0]['skipped'])

    def test_selector_order_is_not_treated_as_a_duplicate(self):
        base = baseline([{'key': 0, 'name': '开屏广告',
                          'rules': {'matches': ['[vid="a"]', '[vid="b"]']}}])
        extra = document([{'key': 1, 'name': '开屏广告',
                           'rules': {'matches': ['[vid="b"]', '[vid="a"]']}}])
        merged, _ = integrate(base, [(source('a'), extra)])
        self.assertEqual(merged['apps'][0]['groups'][1]['rules'][0]['matches'],
                         ['[vid="b"]', '[vid="a"]'])

    def test_unchanged_content_reuses_version_and_changed_content_advances(self):
        base = baseline()
        merged, _ = integrate(base, [])
        self.assertEqual(next_version(merged, base, 1), 2)
        previous = copy.deepcopy(merged)
        previous['version'] = 2
        self.assertEqual(next_version(merged, previous, 1), 2)
        merged['apps'][0]['groups'].append({'key': 0, 'name': '开屏广告',
                                            'rules': '[vid="skip"]'})
        self.assertEqual(next_version(merged, previous, 1), 3)

    def test_polling_policy_differences_do_not_duplicate_a_button_action(self):
        base = baseline([{'key': 0, 'name': '开屏广告', 'fastQuery': True,
                          'matchTime': 9000, 'actionMaximum': 1,
                          'rules': '[vid="skip"]'}])
        extra = document([{'key': 8, 'name': '开屏广告', 'matchTime': 10000,
                           'actionMaximum': 2, 'rules': '[vid="skip"]'}])
        merged, _ = integrate(base, [(source('a'), extra)])
        self.assertEqual(merged['apps'][0]['groups'], base['apps'][0]['groups'])

    def test_version_constraints_keep_distinct_app_variants(self):
        base = baseline([{'key': 0, 'name': '开屏广告',
                          'rules': {'matches': '[vid="skip"]', 'versionCode': 1}}])
        extra = document([{'key': 1, 'name': '开屏广告',
                           'rules': {'matches': '[vid="skip"]', 'versionCode': 2}}])
        merged, _ = integrate(base, [(source('a'), extra)])
        self.assertEqual(merged['apps'][0]['groups'][1]['rules'][0]['versionCode'], 2)

    def test_ad_label_does_not_allow_automatic_agreement_or_payment(self):
        extra = document([
            {'key': 0, 'name': '全屏广告-支付页操作', 'rules': '[text="确认"]'},
            {'key': 1, 'name': '全屏广告', 'rules': '[text="同意并继续"]'},
            {'key': 2, 'name': '全屏广告', 'rules': '[text="直接支付"]'},
            {'key': 3, 'name': '全屏广告', 'rules': '[vid="close_ad"]'},
        ])
        merged, _ = integrate(baseline(), [(source('a'), extra)])
        self.assertEqual([g['rules'] for g in merged['apps'][0]['groups']],
                         [[{'matches': '[vid="close_ad"]'}]])


if __name__ == '__main__':
    unittest.main()
