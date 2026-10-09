import json, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class ProjectSanityTests(unittest.TestCase):
    def test_config_is_staging_locked(self):
        c=json.loads((ROOT/'config.json').read_text())
        self.assertEqual(c['mode'],'authorized_staging_qa')
        self.assertTrue(c['base_url'].startswith('https://'))
        self.assertIn('staging.example.com',c['allowed_hosts'])
    def test_required_selectors_exist(self):
        c=json.loads((ROOT/'config.json').read_text())
        for k in ['username','password','login_submit','question','option','submit','correct_indicator','incorrect_indicator']:
            self.assertIn(k,c['selectors'])
    def test_answers_are_explicit_fixtures(self):
        d=json.loads((ROOT/'test_answers.json').read_text())
        self.assertTrue(d['questions'])
        for q in d['questions']:
            self.assertIn('question_contains',q)
            self.assertIn('answer_text',q)
    def test_no_real_target_default(self):
        c=json.loads((ROOT/'config.json').read_text())
        self.assertEqual(c['base_url'],'https://staging.example.com/login')
if __name__=='__main__': unittest.main()
