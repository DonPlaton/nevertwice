#!/usr/bin/env python3
"""The extractor is told which language to answer in, not asked to work it out.

Measured on `research/data/supersession_v1.json` - a corpus with no Russian in it at all -
the local model wrote **17 of 123 notes in Russian**, a drift of 0.138. The prompt already
carried the rule: *write in the dominant language of the SESSION content*. A conditional
instruction is one a model can decline, and about one time in seven this one did.

Resolving the condition in Python and stating a single language costs nothing and is a
different kind of ask. The detector has one job that is easy to get wrong: a Russian session
is full of Latin identifiers, paths and code, so a majority vote over letters reads almost
every bilingual transcript as English - which is the population this store is mostly made of.
Cyrillic above a low floor is the signal, because Russian prose does not appear by accident.
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


EN = ("Worked through the retry policy today. The client now retries only on 5xx responses "
      "and never on 4xx, which removed the duplicate-order bug. Ran the suite afterwards. ")
RU = ("Разобрали политику повторов. Клиент повторяет запрос только на 5xx и никогда на 4xx, "
      "это убрало дублирование заказов. После этого прогнали весь набор тестов. ")
CODE = ("def retry(resp):\n    if 500 <= resp.status_code < 600:\n        return True\n"
        "    return False  # see src/http/client.py and tests/test_retry.py\n")

print("\n- a monolingual session is called correctly -")
check("English prose reads as latin", m.dominant_script(EN * 4) == "latin")
check("Russian prose reads as cyrillic", m.dominant_script(RU * 4) == "cyrillic")

print("\n- a Russian session full of code is still a Russian session -")
mixed = RU * 4 + CODE * 8
check("Cyrillic survives a majority of Latin identifiers",
      m.dominant_script(mixed) == "cyrillic",
      f"cyr share {sum(1 for c in mixed if chr(0x400) <= c <= chr(0x4ff)) / max(1, sum(1 for c in mixed if c.isalpha())):.3f}")

print("\n- an English session with a quoted Russian error is still English -")
borrowed = EN * 12 + " The log said: не удалось открыть файл. "
check("a borrowed phrase does not flip the verdict",
      m.dominant_script(borrowed) == "latin")

print("\n- too little text is an abstention, not a guess -")
check("a short sample returns nothing", m.dominant_script("hello") == "")
check("an empty sample returns nothing", m.dominant_script("") == "")

print("\n- the rule names one language, and only one -")
en_rule, ru_rule = m.language_rule(EN * 4), m.language_rule(RU * 4)
check("an English session is told ENGLISH", "ENGLISH" in en_rule and "RUSSIAN" not in en_rule,
      en_rule)
check("a Russian session is told RUSSIAN", "RUSSIAN" in ru_rule and "ENGLISH" not in ru_rule,
      ru_rule)
check("an undecidable session falls back to the conditional rule",
      "dominant language" in m.language_rule("hi"))

print("\n- the prompt actually carries it -")
filled = m.EXTRACTION_PROMPT.format(
    transcript=EN * 4, project_hint="demo", tag_vocab="a, b",
    existing_patterns="(none)", existing_mistakes="(none)", existing_decisions="(none)",
    brain_block="", language_rule=m.language_rule(EN * 4))
check("the resolved rule reaches the prompt", "in ENGLISH" in filled)
check("the conditional wording is gone", "dominant language of the SESSION content (a"
      not in filled)
check("tags stay ASCII regardless", "lowercase ASCII kebab-case" in filled)

print(f"\nextraction language: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
