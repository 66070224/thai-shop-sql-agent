from thai_shop_agent.eval_metrics import ex_match, ex_strict


def both(gold, pred, gold_ncols, pred_ncols, order_matters=False):
    return (ex_match(gold, pred, gold_ncols, pred_ncols, order_matters),
            ex_strict(gold, pred, gold_ncols, pred_ncols, order_matters))


def test_identical():
    assert both([(1, "a")], [(1, "a")], 2, 2) == (True, True)


def test_row_order_ignored_unless_order_matters():
    gold, pred = [(1,), (2,)], [(2,), (1,)]
    assert both(gold, pred, 1, 1) == (True, True)
    assert both(gold, pred, 1, 1, order_matters=True) == (False, False)
    assert both(gold, gold, 1, 1, order_matters=True) == (True, True)


def test_extra_column_allowed_only_in_ex():
    gold = [("Beauty",), ("Shoes",)]
    pred = [("Beauty", 120.5), ("Shoes", 99.0)]
    assert both(gold, pred, 1, 2) == (True, False)


def test_swapped_columns_allowed_only_in_ex():
    gold = [("2024", 10.0), ("2025", 20.0)]
    pred = [(10.0, "2024"), (20.0, "2025")]
    assert both(gold, pred, 2, 2) == (True, False)


def test_missing_column_fails():
    assert both([("a", 1)], [("a",)], 2, 1) == (False, False)


def test_columns_must_stay_paired_row_by_row():
    # each column matches as a multiset, but the pairing across columns is wrong
    gold = [("a", 1), ("b", 2)]
    pred = [("a", 2), ("b", 1)]
    assert both(gold, pred, 2, 2) == (False, False)


def test_one_to_one_mapping_with_duplicate_candidate_columns():
    gold = [(1, 1), (2, 2)]
    assert ex_match(gold, [(1,), (2,)], 2, 1, False) is False  # one pred column can't serve two gold columns
    assert ex_match(gold, [(1, 1), (2, 2)], 2, 2, False) is True


def test_rounding_to_two_decimals():
    assert both([(3.14159,)], [(3.141,)], 1, 1) == (True, True)
    assert both([(3.14,)], [(3.15,)], 1, 1) == (False, False)
    assert both([(2,)], [(2.0,)], 1, 1) == (True, True)
    assert both([(0.0,)], [(-0.001,)], 1, 1) == (True, True)


def test_numbers_and_strings_do_not_mix():
    assert both([("2024",)], [(2024,)], 1, 1) == (False, False)


def test_null_matches_only_null():
    assert both([(None,), (1,)], [(1,), (None,)], 1, 1) == (True, True)
    assert both([(None,)], [(0,)], 1, 1) == (False, False)


def test_empty_results():
    assert both([], [], 1, 1) == (True, True)
    assert both([], [], 1, 3) == (True, False)
    assert both([(1,)], [], 1, 1) == (False, False)
    assert both([], [(1,)], 1, 1) == (False, False)


def test_multiset_counts_duplicates():
    assert both([(1,), (1,), (2,)], [(1,), (2,), (2,)], 1, 1) == (False, False)
    assert both([(1,), (1,), (2,)], [(2,), (1,), (1,)], 1, 1) == (True, True)


def test_extra_rows_fail():
    assert both([(1,)], [(1,), (2,)], 1, 1) == (False, False)
