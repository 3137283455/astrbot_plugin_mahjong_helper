import json,shutil,subprocess,unittest
from astrbot_plugin_mahjong_helper.replay_pages import COPY_SCRIPT

@unittest.skipUnless(shutil.which('node'),'Node unavailable')
class ReplayCopyTests(unittest.TestCase):
    def test_https_http_and_permission_failure_do_not_fake_copy_success(self):
        program=r'''
const vm = require('node:vm');
let input = ''; process.stdin.on('data', d => input += d); process.stdin.on('end', async () => {
  const script = JSON.parse(input); const results = [];
  for (const scenario of ['https', 'denied-legacy', 'http', 'manual']) {
    let handler; let clipboard = ''; let helper; let execCalls = 0;
    const url = 'https://game.maj-soul.com/1/?paipu=261030-42accbcd-b566-4919-88e7-28fd2e5a5d46_a12105509';
    const element = () => ({ value: '', hidden: true, textContent: '', style: {}, focus(){}, select(){}, setSelectionRange(){}, remove(){}, scrollIntoView(){}, addEventListener(){} });
    const elements = Object.fromEntries(['copy-status','copy-fallback','copy-value','copy-close'].map(k => [k, element()]));
    const button = {dataset:{copyReplay:url},textContent:'复制牌谱'};
    const document = {
      getElementById:k => elements[k], addEventListener:(name, fn) => {handler = fn;},
      createElement:() => {helper = element(); return helper;}, body:{appendChild(){}},
      execCommand:() => {execCalls++; if(scenario === 'manual') return false; clipboard = helper.value; return true;}
    };
    const navigator = {clipboard:{writeText:async text => {if(scenario === 'denied-legacy') throw Error('denied'); clipboard = text;}}};
    vm.runInNewContext(script,{document,navigator,window:{isSecureContext:['https','denied-legacy'].includes(scenario)},setTimeout(){return 1;},clearTimeout(){}});
    await handler({target:{closest:() => button}});
    results.push({scenario,clipboard,text:button.textContent,status:elements['copy-status'].textContent,
                  manual:!elements['copy-fallback'].hidden,value:elements['copy-value'].value,execCalls});
  }
  process.stdout.write(JSON.stringify(results));
});'''
        process=subprocess.run(['node','-e',program],input=json.dumps(COPY_SCRIPT),capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(process.returncode,0,process.stderr)
        rows=json.loads(process.stdout)
        for row in rows[:3]:
            self.assertEqual(row['text'],'已复制')
            self.assertTrue(row['clipboard'].endswith('_a12105509'))
            self.assertFalse(row['manual'])
        self.assertEqual(rows[0]['execCalls'],0)
        self.assertEqual(rows[1]['execCalls'],1)
        self.assertEqual(rows[2]['execCalls'],1)
        self.assertTrue(rows[3]['manual']);self.assertEqual(rows[3]['clipboard'],'')
        self.assertEqual(rows[3]['text'],'复制牌谱')
        self.assertTrue(rows[3]['value'].endswith('_a12105509'))
