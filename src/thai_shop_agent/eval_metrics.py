"""Execution-accuracy metrics: compare a predicted result set with the gold one.

EX: every gold column must map 1:1 onto a distinct predicted column; after projecting the
prediction onto those columns, the rows must match as a multiset (as a sequence when order
matters). Extra predicted columns and a different column order are allowed.
EX-strict: same number of columns, in the same order.
Numbers are rounded to 2 decimals (so 2 == 2.0); NULL only matches NULL; strings must match exactly.
"""

from collections import Counter
from collections.abc import Sequence

Row = Sequence[object]


def normalize(value: object) -> object:
    if isinstance(value, bool):
        return round(float(value), 2)
    if isinstance(value, (int, float)):
        return round(float(value), 2) + 0.0  # + 0.0 folds -0.0 into 0.0
    return value


def _norm_rows(rows: Sequence[Row]) -> list[tuple]:
    return [tuple(normalize(v) for v in row) for row in rows]


def _rows_equal(gold: list[tuple], pred: list[tuple], order_matters: bool) -> bool:
    if len(gold) != len(pred):
        return False
    return gold == pred if order_matters else Counter(gold) == Counter(pred)


def ex_strict(gold_rows: Sequence[Row], pred_rows: Sequence[Row], gold_ncols: int, pred_ncols: int,
              order_matters: bool) -> bool:
    if gold_ncols != pred_ncols:
        return False
    return _rows_equal(_norm_rows(gold_rows), _norm_rows(pred_rows), order_matters)


def ex_match(gold_rows: Sequence[Row], pred_rows: Sequence[Row], gold_ncols: int, pred_ncols: int,
             order_matters: bool) -> bool:
    if pred_ncols < gold_ncols:
        return False
    gold, pred = _norm_rows(gold_rows), _norm_rows(pred_rows)
    if len(gold) != len(pred):
        return False
    if not gold:
        return True

    def column(rows: list[tuple], j: int) -> list:
        return [r[j] for r in rows]

    def same_column(a: list, b: list) -> bool:
        return a == b if order_matters else Counter(a) == Counter(b)

    # Candidate predicted columns per gold column (necessary condition), then backtrack over
    # injective assignments and test the full projected row multiset.
    candidates = [
        [j for j in range(pred_ncols) if same_column(column(gold, i), column(pred, j))]
        for i in range(gold_ncols)
    ]

    def search(i: int, used: list[int]) -> bool:
        if i == gold_ncols:
            projected = [tuple(r[j] for j in used) for r in pred]
            return _rows_equal(gold, projected, order_matters)
        return any(search(i + 1, used + [j]) for j in candidates[i] if j not in used)

    return search(0, [])
