"""Count the most common words in a piece of text."""


def top_words(text, n):
    """Return the `n` most common words as (word, count) pairs, most common first."""
    counts = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: -pair[1])
    return ranked[:n]
