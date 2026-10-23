"""Top-five display keywords and legacy dense input; lexical search uses lexical.py."""

import re
import unicodedata
from collections import Counter

VERSION = "nfc-casefold-unigram-stopwords-v1"
STOPWORDS = frozenset("và là của có các những một được trong cho với về từ đến này đó khi thì mà hoặc không đã đang sẽ the a an of and to in is are for on".split())


class UnigramKeywords:
    version = VERSION

    def extract(self, text: str) -> list[str]:
        tokens = re.findall(r"[^\W\d_]+", unicodedata.normalize("NFC", text).casefold())
        counts = Counter(token for token in tokens if len(token) > 1 and token not in STOPWORDS)
        return sorted(sorted(counts, key=lambda token: (-counts[token], token))[:5])
