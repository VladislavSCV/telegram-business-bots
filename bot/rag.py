"""Retrieval over a Markdown knowledge base: heading-aware chunks + BM25 ranking.

No vector database is needed for a knowledge base of a few dozen pages; BM25 with light Russian
stemming finds the right fragments reliably and runs in microseconds. The fragments are then
passed to the LLM as the only allowed source of facts.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return text.split()


# Common Russian endings, longest first; stripped so that "гарантия"/"гарантии"/"гарантию" match.
ENDINGS = sorted(
    _words(
        "ами ями ого его ому ему ыми ими ой ей ий ый ая яя ое ее ые ие ом ем ах ях ам ям ов ев ию ия ии ью "
        "а я о е ы и у ю ь"
    ),
    key=len,
    reverse=True,
)
# Function words and generic verbs that otherwise match unrelated fragments ("делаете" ~ "дизайнер делает").
STOP_WORDS = set(
    _words(
        "и в во на по с со к ко о об от до за из у же ли не ни а но да или как что это для при вы мы я ты "
        "есть можно нужно надо будет делаете делаем делает делать какой какая какие каких чего чем у вас ваш ваши"
    )
)
# Query words mapped to the vocabulary the knowledge base uses.
SYNONYMS = {
    "стоит": "цен",
    "стоимост": "цен",
    "скольк": "цен",
    "почем": "цен",
    "прайс": "цен",
    "цены": "цен",
    "рассрочк": "рассрочк",
    "кредит": "рассрочк",
    "сделает": "изготовлен",
    "изготовят": "изготовлен",
}


def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if len(word) <= 3:
        return word
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def tokenize(text: str) -> list[str]:
    tokens = (stem(w) for w in WORD_RE.findall(text) if w.lower() not in STOP_WORDS)
    return [SYNONYMS.get(t, t) for t in tokens]


@dataclass(frozen=True)
class Chunk:
    title: str
    text: str


def split_markdown(text: str, source: str, max_chars: int = 700) -> list[Chunk]:
    """Split by headings, then by paragraphs so that every chunk stays under `max_chars`."""
    chunks: list[Chunk] = []
    title = source
    buffer: list[str] = []

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            chunks.append(Chunk(title=title, text=body))
        buffer.clear()

    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s+(.*)", line)
        if heading:
            flush()
            title = heading.group(1).strip()
            continue
        if not line.strip() and sum(len(x) for x in buffer) > max_chars:
            flush()
            continue
        buffer.append(line)
    flush()
    return chunks


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        # Titles are indexed twice: a heading like "Гарантия" is a strong signal.
        self.docs = [Counter(tokenize(f"{c.title} {c.title} {c.text}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        frequency = Counter(term for doc in self.docs for term in doc)
        total = len(self.docs)
        self.idf = {term: math.log(1 + (total - n + 0.5) / (n + 0.5)) for term, n in frequency.items()}

    @classmethod
    def from_directory(cls, directory: str | Path) -> "KnowledgeBase":
        chunks: list[Chunk] = []
        for path in sorted(Path(directory).glob("*.md")):
            chunks.extend(split_markdown(path.read_text(encoding="utf-8"), source=path.stem))
        return cls(chunks)

    def search(self, query: str, k: int = 3, min_score: float = 0.5) -> list[tuple[Chunk, float]]:
        terms = tokenize(query)
        scored = []
        for chunk, doc, length in zip(self.chunks, self.docs, self.lengths, strict=True):
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if tf:
                    norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg_length))
                    score += self.idf.get(term, 0) * norm
            if score >= min_score:
                scored.append((chunk, round(score, 3)))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:k]
