"""`nevertwice/stemmer.py`: the lexical arm's morphology, pinned against reference stemmers.

The English vectors are NLTK's PorterStemmer in `ORIGINAL_ALGORITHM` mode; the Russian ones are
`py_rust_stemmers`' Snowball (the library behind Mem0's fastembed BM25). Both references live
in the polygon venvs, never in this repository - the point of the port is zero dependencies -
so the expected values are frozen here (generated 2026-09-06; 100% agreement on 23,268 English
tokens of LoCoMo + LongMemEval and on every non-`ё` Russian token of the vault vocabulary).
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import stemmer as st  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


EN = [
    ("running", "run"), ("runs", "run"), ("caresses", "caress"), ("ponies", "poni"),
    ("ties", "ti"), ("cats", "cat"), ("feed", "feed"), ("agreed", "agre"),
    ("plastered", "plaster"), ("motoring", "motor"), ("sing", "sing"), ("conflated", "conflat"),
    ("troubled", "troubl"), ("sized", "size"), ("hopping", "hop"), ("tanned", "tan"),
    ("falling", "fall"), ("hissing", "hiss"), ("fizzed", "fizz"), ("failing", "fail"),
    ("filing", "file"), ("happy", "happi"), ("sky", "sky"), ("relational", "relat"),
    ("conditional", "condit"), ("rational", "ration"), ("valency", "valenc"), ("hesitancy", "hesit"),
    ("digitizer", "digit"), ("conformably", "conform"), ("radically", "radic"), ("differently", "differ"),
    ("vilely", "vile"), ("analogously", "analog"), ("vietnamization", "vietnam"), ("predication", "predic"),
    ("operator", "oper"), ("feudalism", "feudal"), ("decisiveness", "decis"), ("hopefulness", "hope"),
    ("callousness", "callous"), ("formality", "formal"), ("sensitivity", "sensit"), ("sensibility", "sensibl"),
    ("triplicate", "triplic"), ("formative", "form"), ("formalize", "formal"), ("electricity", "electr"),
    ("electrical", "electr"), ("hopeful", "hope"), ("goodness", "good"), ("revival", "reviv"),
    ("allowance", "allow"), ("inference", "infer"), ("airliner", "airlin"), ("gyroscopic", "gyroscop"),
    ("adjustable", "adjust"), ("defensible", "defens"), ("irritant", "irrit"), ("replacement", "replac"),
    ("adjustment", "adjust"), ("dependent", "depend"), ("adoption", "adopt"), ("homologous", "homolog"),
    ("communism", "commun"), ("activate", "activ"), ("angularity", "angular"), ("effective", "effect"),
    ("bowdlerize", "bowdler"), ("probate", "probat"), ("rate", "rate"), ("cease", "ceas"),
    ("controlling", "control"), ("rolling", "roll"), ("embeddings", "embed"), ("embedding", "embed"),
    ("cached", "cach"), ("caching", "cach"), ("retries", "retri"), ("timeout", "timeout"),
    ("tokens", "token"), ("generalization", "gener"), ("connections", "connect"), ("databases", "databas"),
    ("indexes", "index"), ("indices", "indic"), ("queries", "queri"), ("refactoring", "refactor"),
    ("migrations", "migrat"), ("deployed", "deploi"), ("deployment", "deploy"), ("latencies", "latenc"),
    ("guards", "guard"), ("injected", "inject"), ("injection", "inject"), ("supersession", "supersess"),
    ("retrieval", "retriev"), ("retrieved", "retriev"), ("ranking", "rank"), ("ranked", "rank"),
    ("memory", "memori"), ("memories", "memori"), ("sessions", "session"), ("transcripts", "transcript"),
    ("watermark", "watermark"), ("embedded", "embed"), ("analysis", "analysi"), ("analyses", "analys"),
    ("status", "statu"), ("class", "class"), ("classes", "class"), ("process", "process"),
    ("processes", "process"), ("was", "wa"), ("this", "thi"), ("gas", "ga"),
    ("tests", "test"), ("testing", "test"), ("tested", "test"), ("files", "file"),
    ("configuration", "configur"), ("configured", "configur"),
]

RU = [
    ("ошибка", "ошибк"), ("ошибки", "ошибк"), ("ошибку", "ошибк"), ("ошибкой", "ошибк"), ("ошибках", "ошибк"),
    ("тесты", "тест"), ("тестов", "тест"), ("тестами", "тест"), ("запуск", "запуск"), ("запуска", "запуск"),
    ("запуском", "запуск"), ("эмбеддинги", "эмбеддинг"), ("эмбеддингов", "эмбеддинг"), ("локальный", "локальн"), ("локальная", "локальн"),
    ("локальные", "локальн"), ("локального", "локальн"), ("локальной", "локальн"), ("работает", "работа"), ("работают", "работа"),
    ("работал", "работа"), ("работала", "работа"), ("работать", "работа"), ("запустить", "запуст"), ("запущен", "запущ"),
    ("запущенный", "запущен"), ("данных", "дан"), ("данные", "дан"), ("ранний", "ран"), ("модель", "модел"),
    ("модели", "модел"), ("моделей", "модел"), ("памяти", "памят"), ("память", "памя"), ("заметки", "заметк"),
    ("заметка", "заметк"), ("заметок", "заметок"), ("сессии", "сесс"), ("сессия", "сесс"), ("проект", "проект"),
    ("проекта", "проект"), ("проектов", "проект"), ("хранилище", "хранилищ"), ("хранилища", "хранилищ"), ("векторы", "вектор"),
    ("вектора", "вектор"), ("индексация", "индексац"), ("индексации", "индексац"), ("быстрее", "быстр"), ("быстро", "быстр"),
    ("медленно", "медлен"), ("новейший", "нов"), ("красивейшая", "красив"), ("делавший", "дела"), ("сделавший", "сдела"),
    ("делаясь", "дел"), ("бегавшая", "бега"), ("читающий", "чита"), ("читающая", "чита"), ("скорость", "скорост"),
    ("скорости", "скорост"), ("прав", "прав"), ("гало", "гал"), ("мало", "мал"), ("скана", "скан"),
    ("фан", "фан"), ("дает", "дает"), ("данном", "дан"),
]

print("\n- Porter 1980 against the NLTK reference -")
bad = [(w, st.stem_en(w), s) for w, s in EN if st.stem_en(w) != s]
check(f"all {len(EN)} English vectors match", not bad, str(bad[:5]))
check("the classic pairs", st.stem_en("caresses") == "caress" and st.stem_en("ponies") == "poni"
      and st.stem_en("relational") == "relat" and st.stem_en("generalization") == "gener")
check("inflections of one word meet at one stem",
      len({st.stem_en(w) for w in ("connect", "connected", "connection", "connections")}) == 1
      and len({st.stem_en(w) for w in ("cache", "cached", "caching", "caches")}) == 1)
# Porter's own quirk, recorded rather than asserted: `embed` loses its `ed` (-> `emb`) while
# `embedded`/`embedding`/`embeddings` stem to `embed`. The reference does the same.
check("the reference's quirks are reproduced, not repaired",
      st.stem_en("embed") == "emb" and st.stem_en("embeddings") == "embed")

print("\n- Snowball Russian against py_rust_stemmers -")
bad = [(w, st.stem_ru(w), s) for w, s in RU if st.stem_ru(w) != s]
check(f"all {len(RU)} Russian vectors match", not bad, str(bad[:5]))
check("the noun paradigm meets at one stem",
      len({st.stem_ru(w) for w in ("ошибка", "ошибки", "ошибку", "ошибкой", "ошибках")}) == 1)
check("the adjective paradigm meets at one stem",
      len({st.stem_ru(w) for w in ("локальный", "локальная", "локальные", "локального")}) == 1)
check("the preceding-letter test respects RV (данных -> дан, not да)", st.stem_ru("данных") == "дан")
check("yo folds to e before stemming, so both spellings meet",
      st.stem_ru("отчёт") == st.stem_ru("отчет") == "отчет" and st.stem_ru("отчёты") == "отчет")
check("a word with no vowel is returned as is", st.stem_ru("вскр") == "вскр")

print("\n- dispatch, stop words, normalise -")
check("Latin goes to Porter, Cyrillic to Snowball",
      st.stem("running") == "run" and st.stem("тесты") == "тест")
check("digits and mixed tokens pass through",
      st.stem("2026") == "2026" and st.stem("v2") == "v2" and st.stem("") == "")
check("non-ASCII Latin script passes through (no stemmer claims it)", st.stem("naïve") == "naïve")
check("other scripts pass through", st.stem("記憶") == "記憶")
check("stop words in both languages",
      st.is_stop("the") and st.is_stop("это") and not st.is_stop("timeout"))
out = st.normalise(["the", "embeddings", "were", "cached", "twice", "cached"])
check("normalise drops stop words, stems the rest, keeps order and counts",
      out == ["embed", "cach", "twice", "cach"], str(out))
check("normalise can keep stop words or skip stemming",
      st.normalise(["the", "cached"], stop=False) == ["the", "cach"]
      and st.normalise(["the", "cached"], stemming=False) == ["cached"])
check("stemming is cached (a second call is a dict hit)",
      st.stem_en.cache_info().hits > 0 and st.stem_ru.cache_info().hits > 0)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
