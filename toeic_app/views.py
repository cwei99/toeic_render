import random
import re
import os
import json
import pandas as pd
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, 'vocabulary.csv')

LEVEL_ORDER        = ["初級", "中級", "高級"]
MASTERED_THRESHOLD = 3

# 遺忘曲線間隔表
GAP_BY_COUNT = [0, 5, 15, 100]

def _get_gap(correct_count):
    idx = min(correct_count, len(GAP_BY_COUNT) - 1)
    return GAP_BY_COUNT[idx]

# ── 不規則動詞：surface → (base, tag) ────────────────────────────
_IRREGULAR_VERBS: dict = {
    'stuck':    ('stick', 'VBD'), 'sticks':   ('stick', 'VBZ'), 'sticking': ('stick', 'VBG'),
    'led':      ('lead',  'VBD'), 'leads':    ('lead',  'VBZ'), 'leading':  ('lead',  'VBG'),
    'rose':     ('rise',  'VBD'), 'risen':    ('rise',  'VBN'), 'rises':    ('rise',  'VBZ'), 'rising':   ('rise',  'VBG'),
    'arose':    ('arise', 'VBD'), 'arisen':   ('arise', 'VBN'), 'arises':   ('arise', 'VBZ'), 'arising':  ('arise', 'VBG'),
}

# 不規則動詞：(base, tag) → surface（用於 _inflect_word）
_IRREGULAR_VERBS_FORWARD: dict = {
    ('stick', 'VBD'): 'stuck',  ('stick', 'VBZ'): 'sticks',  ('stick', 'VBG'): 'sticking',
    ('lead',  'VBD'): 'led',    ('lead',  'VBZ'): 'leads',   ('lead',  'VBG'): 'leading',
    ('rise',  'VBD'): 'rose',   ('rise',  'VBN'): 'risen',   ('rise',  'VBZ'): 'rises',   ('rise',  'VBG'): 'rising',
    ('arise', 'VBD'): 'arose',  ('arise', 'VBN'): 'arisen',  ('arise', 'VBZ'): 'arises',  ('arise', 'VBG'): 'arising',
}


def _build_regular_forms(base: str) -> dict:
    """規則動詞變化：回傳 {tag: form}"""
    forms = {}
    # VBZ
    if re.search(r'(?:s|sh|ch|x|z)$', base):
        forms['VBZ'] = base + 'es'
    elif re.search(r'[^aeiou]y$', base):
        forms['VBZ'] = base[:-1] + 'ies'
    else:
        forms['VBZ'] = base + 's'
    # VBD / VBN
    if base.endswith('e'):
        past = base + 'd'
    elif re.search(r'[^aeiou]y$', base):
        past = base[:-1] + 'ied'
    elif re.search(r'^.+[^aeiou][aeiou][^aeiouywx]$', base):
        past = base + base[-1] + 'ed'
    else:
        past = base + 'ed'
    forms['VBD'] = past
    forms['VBN'] = past
    # VBG
    if base.endswith('ie'):
        forms['VBG'] = base[:-2] + 'ying'
    elif base.endswith('e') and not base.endswith('ee'):
        forms['VBG'] = base[:-1] + 'ing'
    elif re.search(r'^.+[^aeiou][aeiou][^aeiouywx]$', base):
        forms['VBG'] = base + base[-1] + 'ing'
    else:
        forms['VBG'] = base + 'ing'
    return forms


def _inflect_word(word: str, tag) -> str:
    if not tag:
        return re.sub(r'\(.*?\)', '', word.split('/')[0]).strip()
    base = re.sub(r'\(.*?\)', '', word.split('/')[0]).strip().lower()
    if tag in ('VB', 'VBP'):
        return base
    if (base, tag) in _IRREGULAR_VERBS_FORWARD:
        return _IRREGULAR_VERBS_FORWARD[(base, tag)]
    return _build_regular_forms(base).get(tag, base)


# ── 不規則名詞複數 ────────────────────────────────────────────────
_IRREGULAR_NOUNS = {
    'shelves': 'shelf', 'knives': 'knife', 'leaves': 'leaf',
    'loaves': 'loaf', 'wolves': 'wolf', 'halves': 'half',
    'scarves': 'scarf', 'lives': 'life', 'wives': 'wife',
    'thieves': 'thief', 'feet': 'foot', 'teeth': 'tooth',
    'men': 'man', 'women': 'woman', 'children': 'child',
    'mice': 'mouse', 'geese': 'goose', 'oxen': 'ox',
    'people': 'person', 'criteria': 'criterion', 'phenomena': 'phenomenon',
    'data': 'datum', 'media': 'medium', 'analyses': 'analysis',
    'bases': 'basis', 'crises': 'crisis', 'theses': 'thesis',
}
_NOUN_PLURAL = {v: k for k, v in _IRREGULAR_NOUNS.items()}

# ── 載入單字表 ────────────────────────────────────────────────────
try:
    _df       = pd.read_csv(CSV_PATH)
    WORD_POOL = _df.to_dict('records')
except Exception as e:
    print(f"[WARNING] Cannot load vocabulary.csv: {e}")
    WORD_POOL = []

LEVEL_WORDS  = {lv: [w['word'] for w in WORD_POOL if str(w['toeic_target']) == lv]
                for lv in LEVEL_ORDER}
LEVEL_COUNTS = {lv: len(LEVEL_WORDS[lv]) for lv in LEVEL_ORDER}
VERB_POOL    = [w for w in WORD_POOL if re.search(r'\bv\.', str(w.get('translation', '')))]

# ── 同義詞表 ──────────────────────────────────────────────────────
SYNONYM_GROUPS: list = []
_SYNONYM_LOOKUP: dict = {}  # word -> set of synonyms

_SYNONYMS_PATH = os.path.join(BASE_DIR, 'synonyms.json')
try:
    with open(_SYNONYMS_PATH, encoding='utf-8') as _f:
        SYNONYM_GROUPS = json.load(_f)
    for _group in SYNONYM_GROUPS:
        for _w in _group:
            _SYNONYM_LOOKUP[_w] = set(_group) - {_w}
    print(f"[INFO] 載入 {len(SYNONYM_GROUPS)} 組同義詞")
except Exception as _e:
    print(f"[WARNING] 無法載入 synonyms.json: {_e}")

# ── 預建 surface → (base, tag) 對照表（純 Python）─────────────────
_SURFACE_TO_BASE: dict = {}

for _entry in VERB_POOL:
    for _part in _entry['word'].split('/'):
        _base = re.sub(r'\(.*?\)', '', _part).strip().lower()
        if not _base:
            continue
        # 不規則變化形
        for _surface, (_b, _tag) in _IRREGULAR_VERBS.items():
            if _b == _base and _surface not in _SURFACE_TO_BASE:
                _SURFACE_TO_BASE[_surface] = (_base, _tag)
        # 規則變化形
        for _tag, _form in _build_regular_forms(_base).items():
            if _form not in _SURFACE_TO_BASE:
                _SURFACE_TO_BASE[_form] = (_base, _tag)

_INFLECT_TAG = {'VBD', 'VBZ', 'VBP', 'VBG', 'VBN', 'VB'}


def _make_blank_pattern(w):
    if w.endswith('y'):
        stem    = re.escape(w[:-1])
        base    = re.escape(w)
        pattern = rf'\b(?:{base}\w*|{stem}(?:ies|ied))\b'
    else:
        base    = re.escape(w)
        pattern = rf'\b{base}\w*\b'
    return re.compile(pattern, re.IGNORECASE)


def _find_and_blank(sentence, target_word, is_verb):
    base_forms = set()
    for part in target_word.split('/'):
        base = re.sub(r'\(.*?\)', '', part).strip().lower()
        if base:
            base_forms.add(base)

    # 斜線變體合併
    parts = [re.sub(r'\(.*?\)', '', p).strip() for p in target_word.split('/') if p.strip()]
    if len(parts) >= 2:
        for i, a in enumerate(parts):
            for b in parts[i+1:]:
                slash_pat = re.compile(
                    rf'\b{re.escape(a)}/{re.escape(b)}\b|\b{re.escape(b)}/{re.escape(a)}\b',
                    re.IGNORECASE
                )
                sentence = slash_pat.sub(a, sentence)

    # 方法一：預建對照表（處理不規則 / 規則動詞變化形）
    if is_verb:
        for m in re.finditer(r'\b\w+\b', sentence):
            surface = m.group(0).lower()
            if surface in _SURFACE_TO_BASE:
                orig_base, tag = _SURFACE_TO_BASE[surface]
                if orig_base in base_forms:
                    blanked = sentence[:m.start()] + '______' + sentence[m.end():]
                    return blanked, tag
            if surface in base_forms:
                blanked = sentence[:m.start()] + '______' + sentence[m.end():]
                return blanked, 'VB'

    # 方法二：不規則名詞複數
    if not is_verb:
        for base_f in base_forms:
            plural = _NOUN_PLURAL.get(base_f)
            targets = [base_f]
            if plural:
                targets.append(plural)
            for t in targets:
                pat = re.compile(rf'\b{re.escape(t)}\b', re.IGNORECASE)
                if pat.search(sentence):
                    return pat.sub('______', sentence), None

    # 方法三：regex fallback
    for part in target_word.split('/'):
        base_w = re.sub(r'\(.*?\)', '', part).strip()
        if not base_w:
            continue
        pat = _make_blank_pattern(base_w)
        m   = pat.search(sentence)
        if m:
            return pat.sub('______', sentence), None

    return sentence, None


def _get_question(difficulty, history, correct_counts):
    max_idx  = LEVEL_ORDER.index(difficulty) if difficulty in LEVEL_ORDER else 2
    eligible = [w for w in WORD_POOL
                if str(w['toeic_target']) in LEVEL_ORDER[:max_idx + 1]]
    if not eligible:
        return None

    last_pos: dict = {}
    for i, w in enumerate(history):
        if w not in last_pos:
            last_pos[w] = i + 1

    def _ok(word):
        pos = last_pos.get(word)
        if pos is None:
            return True
        gap = _get_gap(correct_counts.get(word, 0))
        return pos >= gap

    new_ones = [w for w in eligible if w['word'] not in correct_counts and _ok(w['word'])]
    old_ones = [w for w in eligible if w['word'] in correct_counts and _ok(w['word'])]
    all_pool = new_ones + old_ones

    if not all_pool:
        candidates = sorted(eligible, key=lambda w: last_pos.get(w['word'], 9999), reverse=True)
        all_pool = candidates[:max(1, len(candidates) // 3)]

    review_ones = [w for w in old_ones if 0 < correct_counts.get(w['word'], 0) < MASTERED_THRESHOLD]
    if review_ones and random.random() > 0.6:
        target_raw = random.choice(review_ones)
    else:
        target_raw = random.choice(all_pool)

    for _attempt in range(5):
        if _attempt > 0:
            target_raw = random.choice(all_pool)

        target = target_raw.copy()

        raw_s = str(target.get('sentence', ""))
        raw_t = str(target.get('trans_s', ""))
        if re.search(r'\d+\.', raw_s):
            s_parts   = [p.strip() for p in re.split(r'\d+\.', raw_s) if p.strip()]
            t_parts   = [p.strip() for p in re.split(r'\d+\.', raw_t) if p.strip()]
            idx       = random.randrange(len(s_parts))
            display_s = s_parts[idx]
            display_t = t_parts[idx] if idx < len(t_parts) else raw_t
        else:
            display_s = raw_s.strip()
            display_t = raw_t.strip()

        is_verb = bool(re.search(r'\bv\.', str(target.get('translation', ''))))
        final_sentence, matched_tag = _find_and_blank(display_s, target['word'], is_verb)

        if '______' in final_sentence:
            break

    if is_verb and matched_tag:
        answer_display = _inflect_word(target['word'], matched_tag)
    else:
        answer_display = re.sub(r'\(.*?\)', '', target['word'].split('/')[0]).strip()

    # 排除同義詞當干擾選項
    synonyms_of_target = set()
    target_base = re.sub(r'\(.*?\)', '', target['word'].split('/')[0]).strip().lower()
    if target_base in _SYNONYM_LOOKUP:
        synonyms_of_target = _SYNONYM_LOOKUP[target_base]

    distractor_pool = [w for w in (VERB_POOL if is_verb else WORD_POOL)
                       if w['word'] != target['word']
                       and re.sub(r'\(.*?\)', '', w['word'].split('/')[0]).strip().lower()
                       not in synonyms_of_target]
    distractors = random.sample(distractor_pool, min(3, len(distractor_pool)))

    options = []
    for d in distractors:
        dw = _inflect_word(d['word'], matched_tag) if (is_verb and matched_tag) else \
             re.sub(r'\(.*?\)', '', d['word'].split('/')[0]).strip()
        options.append({"word": dw, "base_word": d['word'], "trans": d['translation']})

    options.append({"word": answer_display, "base_word": target['word'], "trans": target['translation']})
    random.shuffle(options)

    return {
        "word":           target['word'],
        "answer_display": answer_display,
        "translation":    target['translation'],
        "level":          str(target.get('toeic_target', '')),
        "sentence":       final_sentence,
        "trans_s":        display_t,
        "options":        options,
        "verb_tag":       matched_tag,
    }


def index(request):
    word_data = [
        {
            'word':        w['word'],
            'translation': w.get('translation', ''),
            'sentence':    w.get('sentence', ''),
            'trans_s':     w.get('trans_s', ''),
            'level':       str(w.get('toeic_target', '')),
        }
        for w in WORD_POOL
    ]
    return render(request, 'toeic_app/index.html', {
        'stats':       json.dumps(LEVEL_COUNTS),
        'level_words': json.dumps(LEVEL_WORDS),
        'word_data':   json.dumps(word_data),
    })


@csrf_exempt
@require_POST
def get_question(request):
    try:
        data = json.loads(request.body)
    except Exception:
        data = {}
    difficulty = data.get('difficulty', '中級')
    history    = data.get('history', [])
    counts     = data.get('counts', {})
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except Exception:
            history = []
    if isinstance(counts, str):
        try:
            counts = json.loads(counts)
        except Exception:
            counts = {}

    q = _get_question(difficulty, history, counts)
    if not q:
        return JsonResponse({"error": "no questions"}, status=404)
    return JsonResponse(q)
