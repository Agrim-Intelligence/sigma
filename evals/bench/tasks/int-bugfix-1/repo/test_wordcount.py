from wordcount import top_words


def test_counts_repeated_words():
    assert top_words("a b a c a b", 2) == [("a", 3), ("b", 2)]


def test_n_larger_than_vocabulary():
    assert top_words("x y", 5) == [("x", 1), ("y", 1)]


def test_empty_text():
    assert top_words("", 3) == []


def test_words_that_differ_only_in_case_are_one_word():
    assert top_words("The the", 1) == [("the", 2)]
