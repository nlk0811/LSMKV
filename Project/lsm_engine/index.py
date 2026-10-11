"""
Index — Persistent Full-Text Search Index
==========================================
An inverted index that tokenizes documents and stores per-token posting lists
in the database.  Supports add/remove/search with relevance ranking by TF
(term frequency within the query).

Implementation
--------------
  __idx:{name}:doc:{doc_id}             — raw text of each document
  __idx:{name}:token:{token}:{doc_id}  — posting (empty value); lexicographic
                                          scan gives all docs containing a token

Tokenization: whitespace/punctuation split, lower-case, minimum 2 characters.

Example
-------
    idx = db.index('articles')
    idx.add('doc:1', 'Python is a great language for data science')
    idx.add('doc:2', 'Python and Java are popular programming languages')
    idx.add('doc:3', 'Machine learning with Python')

    idx.search('Python language')   # → ['doc:1', 'doc:3', 'doc:2'] by TF
    idx.search('Java')              # → ['doc:2']
    idx.count()                     # → 3
"""

import re
from collections import Counter
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree

_STOP_WORDS = frozenset({
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for',
    'from', 'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on',
    'or', 'that', 'the', 'to', 'was', 'were', 'will', 'with',
})


def _tokenize(text: str) -> List[str]:
    """Split text into lowercase tokens; drop stop words and short tokens."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if len(w) >= 2 and w not in _STOP_WORDS]


class Index:
    """Persistent full-text search index backed by the database."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _doc_key(self, doc_id: str) -> str:
        return f'__idx:{self._name}:doc:{doc_id}'

    def _token_key(self, token: str, doc_id: str) -> str:
        return f'__idx:{self._name}:token:{token}:{doc_id}'

    def _token_prefix(self, token: str) -> str:
        return f'__idx:{self._name}:token:{token}:'

    # ── write ─────────────────────────────────────────────────────────────────

    def add(self, doc_id: str, text: str, **extra_fields) -> None:
        """Index a document.  Extra keyword fields are also tokenized.

        Example:
            idx.add('post:42', 'How to use Python', title='Python Tutorial')
        """
        # Combine all text sources
        full_text = text + ' ' + ' '.join(str(v) for v in extra_fields.values())
        # Remove old index entries if the document was previously indexed
        if self._db.has(self._doc_key(doc_id)):
            self.remove(doc_id)
        # Store raw text
        self._db.put(self._doc_key(doc_id), full_text)
        # Write inverted index entries
        tokens = set(_tokenize(full_text))
        for token in tokens:
            self._db.put(self._token_key(token, doc_id), '')

    def remove(self, doc_id: str) -> bool:
        """Remove a document and its index entries.  Returns True if existed."""
        stored = self._db.get(self._doc_key(doc_id))
        if stored is None:
            return False
        # Delete posting entries for this doc
        tokens = set(_tokenize(stored))
        pipe   = self._db.pipeline()
        for token in tokens:
            pipe.delete(self._token_key(token, doc_id))
        pipe.delete(self._doc_key(doc_id))
        pipe.execute()
        return True

    def update(self, doc_id: str, text: str, **extra_fields) -> None:
        """Update a document (remove + re-add)."""
        self.add(doc_id, text, **extra_fields)   # add() handles removal

    # ── read ──────────────────────────────────────────────────────────────────

    def search(self, query: str, top_n: int = 10) -> List[str]:
        """Return doc_ids matching query, sorted by relevance (TF score).

        Relevance = number of query tokens found in each document.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: Dict[str, int] = Counter()
        for token in query_tokens:
            prefix = self._token_prefix(token)
            plen   = len(prefix)
            for full_key, _ in self._db.prefix_scan(prefix):
                doc_id = full_key[plen:]
                scores[doc_id] += 1

        return [doc_id for doc_id, _ in scores.most_common(top_n)]

    def get(self, doc_id: str) -> Optional[str]:
        """Return stored text for doc_id, or None if not indexed."""
        return self._db.get(self._doc_key(doc_id))

    def count(self) -> int:
        """Total number of indexed documents."""
        prefix = f'__idx:{self._name}:doc:'
        return sum(1 for _ in self._db.prefix_scan(prefix))

    def clear(self) -> int:
        """Remove all documents from this index.  Returns count removed."""
        all_keys = [k for k, _ in self._db.prefix_scan(f'__idx:{self._name}:')]
        for k in all_keys:
            self._db.delete(k)
        return sum(1 for k in all_keys if ':doc:' in k)

    # ── inspection ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, doc_id: str) -> bool:
        return self._db.has(self._doc_key(doc_id))

    def __repr__(self) -> str:
        return f'Index({self._name!r}, docs={len(self)})'
