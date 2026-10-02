import json
from pathlib import Path

ROOT = Path(__file__).parent / 'locales'
CATALOGS = {lang: json.loads((ROOT / (lang + '.json')).read_text(encoding='utf-8')) for lang in ('ru', 'kk')}


def tr(state, key, **values):
    return CATALOGS[state.get('lang') or 'ru'][key].format(**values)


def label(item, lang):
    # Optional name_kk/name_ru supplied in a deployment-specific mapping.
    return item.get('name_' + (lang or 'ru')) or item['name']
