# v3.33.0 受控升级维护 / Controlled deployment maintenance

此分支基于上游 v3.33.0，保留其每个 AkShare 统计请求30秒的子进程超时与备用源切换。
另保留现有部署的大盘整体300秒等待上限：只在共享复盘锁内部调用实际复盘函数时启用，结束后恢复原信号处理器，再释放锁。
它不延长底层等待，也不会主动启动分析或通知。

市场统计缺失时，中文和英文提示词与报告数据块明确披露不可用，不把默认零值作为事实。
有效统计（包括仅有平盘股票的样例）仍正常输出。

本次部署设置 `DAILY_MARKET_CONTEXT_ENABLED=false`，避免新版默认开启的可选盘前上下文在个股工作线程内绕过整体复盘时限。
原 `MARKET_REVIEW_REGION=both` 对应的三个市场使用显式 `cn,hk,us` 保留，避免新版 `both` 扩大为五个市场。
实际资源上限、通知和定时参数由部署配置提供；不得把测试中的合成结果当作真实行情或邮件验收。

## Verification

Run the original AkShare timeout/fallback regressions and the new missing-statistics and outer-guard tests with networking disabled.
The Linux integration test interrupts a blocked provider subprocess through the whole-review alarm, verifies worker cleanup, restores the alarm handler, and reacquires the review lock.
Database migration, scheduler startup, cgroup resources and co-host endpoints require separate staged deployment checks.
Real provider availability, paid-model generation and delivered email are only accepted from an eligible natural scheduled cycle.

The maintenance changes do not replace the upstream provider fallback. They preserve the existing deployment's overall review deadline and disclose missing statistics in both Chinese and English.
Production must use source overlays built from this v3.33.0 branch rather than the older deployment's files.
