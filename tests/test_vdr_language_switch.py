"""Four-language UI bindings, isolated from recordings and case content."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from test_vdr_i18n import run_i18n

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'scripts/vdr_review/src'


def test_locale_notifications_are_ordered_noop_safe_and_unsubscribable():
    result = run_i18n("""
const T=I.create(),changes=[];const stop=T.onChange((next,previous)=>changes.push([next,previous]));
T.setLocale('en');T.setLocale('en');T.setLocale('ja');stop();T.setLocale('zh-CN');
console.log(JSON.stringify({changes,locale:T.getLocale(),delete:T.t('action.delete')}));
""")
    assert result == {'changes': [['en', 'zh-TW'], ['ja', 'en']], 'locale': 'zh-CN', 'delete': '删除'}


def test_bindings_only_touch_owned_text_and_attributes_not_controls_or_evidence():
    result = run_i18n("""
const T=I.create({locale:'ja'}),input={value:'播放/船長/原文',selectionStart:2,selectionEnd:4};
function node(attrs,text='',children=[]){return {attrs,textContent:text,children,
 getAttribute(k){return this.attrs[k]||null;},setAttribute(k,v){this.attrs[k]=v;}};}
const leaf=node({'data-i18n':'action.delete'},'刪除'),parent=node({'data-i18n':'action.save'},'DO NOT REPLACE',[input]);
const attrs=node({'data-i18n-placeholder':'workbench.typePlaceholder','data-i18n-title':'action.save'});
const dynamic=node({'data-i18n':'srt.imported','data-i18n-params':'{"count":3,"channel":"A1"}'});
const evidence=node({},'已核/Play/船長'),nodes=[leaf,parent,attrs,dynamic];
const count=T.apply({querySelectorAll(){return nodes;}});
console.log(JSON.stringify({count,leaf:leaf.textContent,parent:parent.textContent,input,
 placeholder:attrs.attrs.placeholder,title:attrs.attrs.title,dynamic:dynamic.textContent,evidence:evidence.textContent}));
""")
    assert result == {'count': 4, 'leaf': '削除', 'parent': 'DO NOT REPLACE',
                      'input': {'value': '播放/船長/原文', 'selectionStart': 2, 'selectionEnd': 4},
                      'placeholder': '聞こえた内容を入力…', 'title': '案件を保存',
                      'dynamic': 'A1 に 3 文を読み込みました', 'evidence': '已核/Play/船長'}


def test_all_bundled_catalogs_have_four_languages_identical_keys_and_parameters():
    source = 'const path=' + json.dumps(SRC.as_posix()) + ';\n'
    for name in ['i18n_core', 'i18n_ui', 'i18n_editor', 'i18n_app']:
        source += f"require(path+'/{name}.js');\n"
    source += "console.log(JSON.stringify(Object.fromEntries(['zh-TW','en','ja','zh-CN'].map(l=>[l,I.getMessages(l)]))));"
    catalogs = run_i18n(source)
    keys = set(catalogs['en'])
    assert len(keys) > 250
    for locale, catalog in catalogs.items():
        assert set(catalog) == keys, locale
        for key, text in catalog.items():
            assert text.strip(), (locale, key)
            assert sorted(re.findall(r'\{([a-zA-Z][a-zA-Z0-9_]*)\}', text)) == sorted(re.findall(r'\{([a-zA-Z][a-zA-Z0-9_]*)\}', catalogs['en'][key])), (locale, key)


class MarkupBindings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.keys = set()
        self.locales = []
        self.in_language = False
        self.stack = []
        self.unbound = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name, value in attrs.items():
            if name in {'data-i18n', 'data-i18n-title', 'data-i18n-placeholder', 'data-i18n-aria-label'}:
                self.keys.add(value)
            if name in {'title', 'placeholder', 'aria-label'} and re.search('[\u3400-\u9fff]', value or ''):
                if 'data-i18n-' + name not in attrs:
                    self.unbound.append((tag, name, value))
        if tag == 'select' and attrs.get('id') == 'uiLanguage':
            self.in_language = True
        if tag == 'option' and self.in_language:
            self.locales.append(attrs['value'])
        if tag not in {'input', 'br', 'meta', 'link', 'hr'}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == 'select':
            self.in_language = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        if not re.search('[\u3400-\u9fff]', text):
            return
        if any(tag in {'style', 'script', 'pre'} or attrs.get('id') == 'uiLanguage' or 'data-i18n' in attrs for tag, attrs in self.stack):
            return
        self.unbound.append(('text', text.strip()))


def test_static_interface_has_four_autonym_options_and_no_unbound_chinese_copy():
    markup = MarkupBindings()
    markup.feed((SRC / 'app.html').read_text(encoding='utf-8'))
    assert markup.locales == ['zh-TW', 'en', 'ja', 'zh-CN']
    assert markup.unbound == []
    code = 'require(' + json.dumps((SRC / 'i18n_ui.js').as_posix()) + ');\n'
    code += 'console.log(JSON.stringify(' + json.dumps(sorted(markup.keys)) + '.filter(k=>!I.has(k))));'
    assert run_i18n(code) == []


def test_initial_language_does_not_depend_on_case_or_recording_timezone():
    source = (SRC / 'app.js').read_text(encoding='utf-8')
    assert 'vdrreview:locale' in source
    assert 'uiLanguage' in source
    # Language is an app preference, never part of the evidence schema.
    project = (SRC / 'project.js').read_text(encoding='utf-8')
    assert 'uiLanguage' not in project and 'vdrreview:locale' not in project
