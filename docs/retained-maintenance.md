# Fork maintenance and upstream synchronization

This fork contains operating and mail-data fixes that may not yet be in an upstream release. `docs/retained-fixes.json` records their status and semantic regression tests. The `Retained maintenance contracts` workflow runs on pushes and pull requests to `main` without calling paid models or sending mail.

For each upstream update:

1. Fetch the intended stable release, verify its commit, and merge it into a maintenance branch descended from this fork's current `main`. Preserve history; do not reset the fork to upstream, force-push, or deploy raw upstream files over protected maintenance mounts.
2. Reconcile every retained entry against the incoming source. Keep unresolved repairs. An entry can become `upstream_verified` only with its upstream URL, commit and verification evidence; its semantic regression tests remain required.
3. Run `python scripts/check_retained_fixes.py`, relevant provider/report/deadline tests, and the normal backend checks. Calendar, timeout and partial-data failures must remain visible, never replaced with plausible zero values or an apparent successful report.
4. Review the full change. Pin the tested image and overlay hashes, preserve production's per-role settings/data/quotas, and verify only the affected containers after a protected rollback is ready.
5. Natural analysis and delivered-email acceptance remain separate from synthetic tests and health checks.

The two Nov22 mail-data repairs are:

- Legacy Tushare/Sina realtime volume remains in **shares**, matching `UnifiedRealtimeQuote` and historical-bar units; invalid/missing values stay missing without dropping a valid price.
- US cash equity indices use the previous and latest **completed NYSE sessions**, waiting15 minutes after the regular/early close. Daily-bar dates, collection timestamps and report date are distinct. VIX is separately dated volatility data, excluded from cash-equity averages/scoring; missing/unverified cash data is disclosed instead of scored.

The VPS remains the unique scheduled daily mail sender. Keep the repository's daily workflow disabled and do not reintroduce its cron during an upstream merge. Do not place production credentials, report bodies, private configuration or backups in this repository.

The sell statistics bucket includes both `reduce` and `sell`. Report summaries therefore label it **减仓/卖出** (`Reduce/Sell`, `비중축소/매도`). Individual signals keep orange for reduce and red for sell; the combined summary must not change individual action semantics or colors.

HK daily recap uses the exact two completed XHKG sessions, allowing the HKEX close auction to run to 16:10/12:10 HKT plus 15 minutes for data publication. This is a conservative clock gate, not proof a provider has published. The Yahoo daily query requests both dates. If the pair is unusable, use a complete Sina pair for that index, never combine bars across providers; if neither qualifies, the index is explicitly unavailable. Coverage, OHLC, return and range in the index section come from the accepted rows; unavailable indices do not enter the score. A missing dataset yields no market signal, while a partial calculation is based only on the disclosed available indices. HKEX schedule: https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading/CAS?sc_lang=en .
