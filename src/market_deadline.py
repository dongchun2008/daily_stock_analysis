"""Bound the Linux scheduler's synchronous market review without leaking workers.

Only installed in the analyzer. No global requests monkey patch is used.
BaseException deliberately escapes provider retry handlers (except Exception).
The CLI boundary converts it into an explicit partial report before notification.
"""
from contextlib import contextmanager
import logging
import signal
import threading
import time

logger = logging.getLogger(__name__)
MARKET_STATS_TIMEOUT_SECONDS = 90
MARKET_REVIEW_TIMEOUT_SECONDS = 300


class MarketDeadlineExceeded(BaseException):
    """Unwind a stuck provider all the way to the report fallback boundary."""


class DeadlineUnavailable(RuntimeError):
    """Do not start an unbounded operation where Unix alarms cannot be used."""


@contextmanager
def market_deadline(seconds, stage):
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, 'setitimer'):
        raise DeadlineUnavailable('bounded market review requires the Linux main thread')
    if seconds <= 0:
        raise ValueError('deadline must be positive')
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    # Retain the earlier enclosing deadline; nested calls cannot extend it.
    if previous_timer[0] > 0 and previous_timer[0] <= seconds:
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()

    def expired(signum, frame):
        raise MarketDeadlineExceeded(f'{stage} exceeded {seconds:g}s')

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            remaining = max(.000001, previous_timer[0] - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


def market_stats_deadline():
    return market_deadline(MARKET_STATS_TIMEOUT_SECONDS, 'market statistics')


def run_bounded_market_review(review, **kwargs):
    try:
        with market_deadline(MARKET_REVIEW_TIMEOUT_SECONDS, 'market review'):
            return review(**kwargs)
    except (MarketDeadlineExceeded, DeadlineUnavailable) as exc:
        logger.warning('大盘复盘未完成，继续发送已完成的个股结果: %s', exc)
        return ('> 大盘复盘未完成：数据获取或分析超过等待上限，已跳过本次大盘部分。'
                '下方仅包含本次已完成的个股分析；不代表完整大盘复盘。')
