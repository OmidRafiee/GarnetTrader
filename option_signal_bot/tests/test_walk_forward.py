"""تست اعتبارسنجی walk-forward.

**چرا این تست‌ها ارزش دارند**

خودِ این ماژول ابزارِ گرفتنِ overfit است. اگر منطقش غلط باشد، خرابی‌اش
بی‌صداست: یک عدد درخشان می‌دهد که هیچ‌وقت در آینده تکرار نمی‌شود — همان
چیزی که قرار بود جلویش را بگیرد.

پس تمرکز روی سه چیز است:

۱. **ترتیب زمانی.** آزمون باید همیشه بعد از آموزش باشد. اگر برعکس شود،
   استراتژی آینده را دیده و کل نتیجه بی‌معناست.
۲. **`None` نباید صفر شود.** «نمونه کافی نبود» با «انتظار صفر» فرق دارد.
۳. **پایداری، نه میانگین.** میانگین بالا با یک برشِ خوش‌شانس هم به دست
   می‌آید.

اجرای انتها-به-انتها روی تاریخچه‌ی **واقعیِ ضبط‌شده‌ی** TSETMC است.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from backtest.signal_backtester import SignalBacktester
from backtest.walk_forward import (
    MIN_SIGNALS_PER_FOLD,
    Fold,
    FoldResult,
    WalkForwardOptimizer,
    WalkForwardResult,
    build_folds,
    format_report,
    parameter_grid,
)
from data.tsetmc_market_data_client import TsetmcMarketDataClient
from data.tsetmc_option_chain_client import FilePayloadSource, TsetmcOptionChainClient

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CHAIN_FIXTURE = FIXTURE_DIR / "tsetmc_option_market_watch.json"
HISTORY_DIR = FIXTURE_DIR / "history"

#: کافی برای گذراندن `is_usable`
ENOUGH = MIN_SIGNALS_PER_FOLD


def _fold(index: int = 0) -> Fold:
    return Fold(index=index, train_start=0, train_end=100, test_end=140)


def _result(train_e, test_e, train_n=ENOUGH, test_n=ENOUGH, index=0) -> FoldResult:
    """یک برش با اعداد داده‌شده — بدون اجرای بک‌تست."""
    return FoldResult(
        fold=_fold(index),
        params={"x": 1},
        train={"total": train_n, "expectancy_pct": train_e},
        test={"total": test_n, "expectancy_pct": test_e},
    )


# ======================================================================
# ساخت برش‌ها — ترتیب زمانی
# ======================================================================
def test_test_window_always_follows_training():
    """قلب walk-forward. اگر آزمون قبل از آموزش بیفتد، آینده دیده شده."""
    for fold in build_folds(400, 120, 40):
        assert fold.train_start < fold.train_end < fold.test_end


def test_windows_have_the_requested_lengths():
    for fold in build_folds(400, 120, 40):
        assert fold.train_days == 120
        assert fold.test_days == 40


def test_folds_advance_by_the_test_length_by_default():
    """پیش‌فرض یعنی بازه‌های آزمونِ پشت‌سرهم و بدون همپوشانی."""
    folds = build_folds(400, 120, 40)
    for earlier, later in pairwise(folds):
        assert later.train_end == earlier.test_end


def test_custom_step_allows_overlapping_windows():
    folds = build_folds(400, 120, 40, step_days=20)
    assert folds[1].train_start - folds[0].train_start == 20


def test_no_fold_runs_past_the_available_history():
    total = 400
    for fold in build_folds(total, 120, 40):
        assert fold.test_end <= total


def test_history_shorter_than_one_fold_gives_nothing():
    """کوتاه‌تر از آموزش+آزمون یعنی هیچ برشی — نه یک برشِ ناقص."""
    assert build_folds(100, 120, 40) == []
    assert build_folds(159, 120, 40) == []
    assert len(build_folds(160, 120, 40)) == 1


@pytest.mark.parametrize(("train", "test"), [(0, 40), (120, 0), (-10, 40), (120, -5)])
def test_non_positive_window_lengths_are_rejected(train, test):
    with pytest.raises(ValueError):
        build_folds(400, train, test)


# ======================================================================
# شبکه‌ی پارامتر
# ======================================================================
def test_grid_is_the_cartesian_product():
    grid = parameter_grid({"a": [1, 2], "b": [3, 4, 5]})
    assert len(grid) == 6
    assert {"a": 1, "b": 3} in grid
    assert {"a": 2, "b": 5} in grid


def test_empty_space_means_one_run_with_defaults():
    """فضای خالی یعنی «پارامتر پیش‌فرض»، نه «هیچ اجرایی»."""
    assert parameter_grid({}) == [{}]


def test_grid_warns_when_it_explodes(caplog):
    """هر ترکیب یک بک‌تست کامل است؛ کاربر باید بداند چه خبر است."""
    with caplog.at_level("WARNING"):
        grid = parameter_grid({"a": list(range(11)), "b": list(range(11))})
    assert len(grid) == 121
    assert "ترکیب" in caplog.text


# ======================================================================
# `None` نباید صفر شود
# ======================================================================
def test_degradation_is_none_when_either_side_is_unknown():
    assert _result(None, 2.0).degradation is None
    assert _result(2.0, None).degradation is None


def test_degradation_is_train_minus_test():
    """مثبت = افت واقعی (آموزش بهتر از آزمون) — نشانه‌ی overfit."""
    assert _result(3.0, 1.0).degradation == pytest.approx(2.0)


def test_negative_degradation_means_test_beat_train():
    """روی داده‌ی کوتاه واقعاً پیش می‌آید؛ نباید علامت جابه‌جا شود."""
    assert _result(1.0, 3.0).degradation == pytest.approx(-2.0)


def test_a_fold_without_samples_is_not_usable():
    assert not _result(1.0, 1.0, test_n=0).is_usable
    assert not _result(1.0, 1.0, train_n=ENOUGH - 1).is_usable
    assert _result(1.0, 1.0).is_usable


def test_unusable_folds_are_excluded_from_the_mean():
    """یک برش با ۲ سیگنال نباید میانگین را جابه‌جا کند."""
    result = WalkForwardResult(
        params={},
        folds=[
            _result(1.0, 4.0, index=0),
            _result(1.0, -99.0, test_n=2, index=1),  # نمونه‌ی ناکافی
        ],
    )
    assert len(result.usable_folds) == 1
    assert result.mean_test_expectancy == pytest.approx(4.0)


def test_all_metrics_are_none_without_any_usable_fold():
    """هیچ‌کدام نباید صفر برگردانند — صفر یک ادعای عددی است."""
    result = WalkForwardResult(params={}, folds=[_result(1.0, 1.0, test_n=0)])
    assert result.mean_test_expectancy is None
    assert result.worst_test_expectancy is None
    assert result.positive_fold_ratio is None
    assert result.mean_degradation is None


def test_selection_uses_test_not_train():
    """معیار انتخاب باید آزمون باشد. آموزش همیشه می‌تواند درخشان باشد."""
    result = WalkForwardResult(
        params={},
        folds=[_result(99.0, 1.0, index=0), _result(99.0, 3.0, index=1)],
    )
    assert result.mean_test_expectancy == pytest.approx(2.0)


# ======================================================================
# پایداری — میانگین کافی نیست
# ======================================================================
def test_worst_fold_is_reported():
    result = WalkForwardResult(
        params={},
        folds=[_result(1.0, 5.0, index=0), _result(1.0, -2.0, index=1)],
    )
    assert result.worst_test_expectancy == pytest.approx(-2.0)


def test_positive_ratio_counts_folds_not_magnitude():
    result = WalkForwardResult(
        params={},
        folds=[
            _result(1.0, 30.0, index=0),
            _result(1.0, -1.0, index=1),
            _result(1.0, -1.0, index=2),
        ],
    )
    # میانگین مثبت است، ولی فقط یک برش از سه
    assert result.mean_test_expectancy > 0
    assert result.positive_fold_ratio == pytest.approx(1 / 3)


def test_one_lucky_fold_is_not_robust():
    """این دقیقاً حالتی است که میانگینِ تنها پنهانش می‌کند."""
    result = WalkForwardResult(
        params={},
        folds=[
            _result(1.0, 30.0, index=0),
            _result(1.0, -1.0, index=1),
            _result(1.0, -1.0, index=2),
        ],
    )
    assert not result.is_robust


def test_a_single_fold_is_never_robust():
    """با یک برش، این walk-forward نیست."""
    result = WalkForwardResult(params={}, folds=[_result(1.0, 5.0)])
    assert not result.is_robust


def test_negative_mean_is_not_robust():
    result = WalkForwardResult(
        params={},
        folds=[_result(1.0, -1.0, index=0), _result(1.0, -2.0, index=1)],
    )
    assert not result.is_robust


def test_consistently_positive_folds_are_robust():
    result = WalkForwardResult(
        params={},
        folds=[
            _result(1.0, 2.0, index=0),
            _result(1.0, 3.0, index=1),
            _result(1.0, -0.5, index=2),
        ],
    )
    assert result.is_robust


def test_to_dict_is_json_friendly():
    import json

    result = WalkForwardResult(
        params={"fast_window": 5},
        folds=[_result(1.0, 2.0, index=0), _result(1.0, 3.0, index=1)],
    )
    data = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
    assert data["params"]["fast_window"] == 5
    assert data["is_robust"] is True
    assert data["usable_folds"] == 2


# ======================================================================
# انتها-به-انتها روی تاریخچه‌ی واقعیِ ضبط‌شده
# ======================================================================
@pytest.fixture
def optimizer() -> WalkForwardOptimizer:
    if not CHAIN_FIXTURE.exists():
        pytest.skip("نمونه‌ی ضبط‌شده نیست؛ با scripts/record_fixtures.py بسازیدش.")

    source = FilePayloadSource(CHAIN_FIXTURE)
    backtester = SignalBacktester(
        market_data=TsetmcMarketDataClient(source, history_dir=HISTORY_DIR),
        option_chain=TsetmcOptionChainClient(source),
        strategies=[],
        horizon_days=5,
        warmup_days=25,
        step_days=3,
        # شبکه در تست ممنوع؛ تعدیل رویداد شرکتی درخواست HTTP می‌زند
        adjust_corporate_actions=False,
    )
    return WalkForwardOptimizer(
        backtester, "directional_ma_cross", train_days=120, test_days=50
    )


SPACE = {"fast_window": [3, 5], "slow_window": [15, 20]}
SYMBOLS = ["خودرو", "شستا"]


def test_runs_end_to_end_on_recorded_history(optimizer):
    results = optimizer.run(SYMBOLS, SPACE, history_days=400)
    assert len(results) == 4, "هر ترکیب باید یک نتیجه بدهد"
    assert all(r.folds for r in results)


def test_every_combination_sees_the_same_folds(optimizer):
    """مقایسه‌ی ترکیب‌ها فقط وقتی معنا دارد که روی یک برش سنجیده شوند."""
    results = optimizer.run(SYMBOLS, SPACE, history_days=400)
    windows = {
        tuple((f.fold.train_start, f.fold.test_end) for f in r.folds) for r in results
    }
    assert len(windows) == 1


def test_results_are_sorted_by_test_expectancy(optimizer):
    results = optimizer.run(SYMBOLS, SPACE, history_days=400)
    scored = [r.mean_test_expectancy for r in results if r.mean_test_expectancy]
    assert scored == sorted(scored, reverse=True)


def test_unknown_scores_sort_last(optimizer):
    """`None` یعنی نمونه کافی نبود؛ نباید بالای فهرست بنشیند."""
    results = optimizer.run(SYMBOLS, SPACE, history_days=400)
    seen_unknown = False
    for result in results:
        if result.mean_test_expectancy is None:
            seen_unknown = True
        elif seen_unknown:
            pytest.fail("ترکیبِ نامعلوم بالاتر از ترکیبِ امتیازدار آمد")


def test_real_data_discriminates_between_parameters(optimizer):
    """اگر همه‌ی ترکیب‌ها یک عدد بدهند، بهینه‌سازی بی‌معناست."""
    results = optimizer.run(SYMBOLS, SPACE, history_days=400)
    scores = {
        round(r.mean_test_expectancy, 4)
        for r in results
        if r.mean_test_expectancy is not None
    }
    assert len(scores) > 1


def test_best_returns_only_a_robust_combination(optimizer):
    best = optimizer.best(SYMBOLS, SPACE, history_days=400)
    if best is not None:
        assert best.is_robust
        assert best.mean_test_expectancy > 0


def test_best_is_the_top_robust_result(optimizer):
    results = optimizer.run(SYMBOLS, SPACE, history_days=400)
    best = optimizer.best(SYMBOLS, SPACE, history_days=400)
    expected = next((r for r in results if r.is_robust), None)
    assert (best is None) == (expected is None)
    if best is not None:
        assert best.params == expected.params


def test_no_result_when_history_is_too_short(optimizer):
    """پیشنهادِ بی‌پشتوانه از نبودِ پیشنهاد بدتر است."""
    assert optimizer.run(SYMBOLS, SPACE, history_days=30) == []


def test_unknown_strategy_raises_loudly(optimizer):
    """نام غلط نباید بی‌صدا نتیجه‌ی خالی بدهد."""
    optimizer.strategy_name = "does_not_exist"
    with pytest.raises(ValueError, match="ناشناخته"):
        optimizer.run(SYMBOLS, SPACE, history_days=400)


def test_a_broken_symbol_does_not_kill_the_run(optimizer):
    results = optimizer.run([*SYMBOLS, "نماد_وجود_ندارد"], SPACE, history_days=400)
    assert results, "نماد خراب باید رد شود، نه اینکه کل اجرا بخوابد"


def test_all_symbols_broken_yields_nothing(optimizer):
    assert optimizer.run(["نماد_الف", "نماد_ب"], SPACE, history_days=400) == []


def test_backtester_strategies_are_restored(optimizer):
    """بهینه‌ساز `strategies` بک‌تستر را موقتاً عوض می‌کند."""
    before = optimizer.backtester.strategies
    optimizer.run(SYMBOLS, SPACE, history_days=400)
    assert optimizer.backtester.strategies is before


# ======================================================================
# گزارش
# ======================================================================
def test_report_emphasises_the_test_number(optimizer):
    results = optimizer.run(SYMBOLS, SPACE, history_days=400)
    text = format_report(results)
    assert "آزمون" in text
    assert "overfit" in text


def test_report_says_so_when_nothing_is_robust():
    weak = [
        WalkForwardResult(
            params={"a": 1},
            folds=[_result(1.0, -1.0, index=0), _result(1.0, -2.0, index=1)],
        )
    ]
    assert "بی‌پشتوانه" in format_report(weak)


def test_report_handles_no_results():
    assert "نتیجه‌ای نداد" in format_report([])


def test_report_shows_unknown_not_zero():
    """گزارش هم باید «نامعلوم» بگوید، نه صفر."""
    unknown = [WalkForwardResult(params={"a": 1}, folds=[_result(1.0, 1.0, test_n=0)])]
    assert "نامعلوم" in format_report(unknown)
