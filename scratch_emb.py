import hashlib
import numpy as np

STOPWORDS = {
    'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or', 'is', 'was', 'are',
    'by', 'with', 'from', 'headline', 'timestamp', 'content', 'source', 'domain', 'entities',
    'location', 'utc', 'http', 'https'
}

def mock_emb(text, filter_stops=True):
    cleaned = text.lower().strip()
    words = [w.strip(".,;:!?\"'()[]{}") for w in cleaned.split() if w.strip(".,;:!?\"'()[]{}")]
    if filter_stops:
        words = [w for w in words if w not in STOPWORDS]
    vec = np.zeros(3072, dtype=np.float32)
    for word in words:
        w_hash = int(hashlib.md5(word.encode('utf-8')).hexdigest()[:12], 16)
        rng = np.random.RandomState(w_hash % (2**32))
        wdir = rng.randn(3072).astype(np.float32)
        norm = np.linalg.norm(wdir)
        if norm > 0:
            vec += wdir / norm
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec

t1 = "Headline: Drone strikes tanker in Strait of Hormuz\nTimestamp: 2026-10-09 14:00 UTC\nDomain: US-Iran conflict\nContent: An explosive drone targeted an oil vessel."
t2 = "Headline: Naval vessel intercepted off Gulf\nTimestamp: 2026-10-09 14:00 UTC\nDomain: US-Iran conflict\nContent: Naval forces confirmed tanker targeted by drone."
t3 = "Headline: Chinese influencer cheated in Berlin marathon\nTimestamp: 2026-10-09 14:00 UTC\nDomain: US-Iran conflict\nContent: A runner used a bicycle in Germany."

print("WITHOUT STOPWORD FILTER:")
e1, e2, e3 = mock_emb(t1, False), mock_emb(t2, False), mock_emb(t3, False)
print("t1-t2:", np.dot(e1, e2), "t1-t3 (marathon!):", np.dot(e1, e3))

print("WITH STOPWORD FILTER:")
e1, e2, e3 = mock_emb(t1, True), mock_emb(t2, True), mock_emb(t3, True)
print("t1-t2:", np.dot(e1, e2), "t1-t3 (marathon!):", np.dot(e1, e3))
