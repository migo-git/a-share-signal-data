# A-share Signal Axis Data Bridge

给“信号轴模型 V0.1”提供稳定的 120 日前复权行情数据。

每个 A 股交易日收盘后，GitHub Actions 自动从 **东方财富** 拉取 `fqt=1` 前复权日 K，并用 **BaoStock** `adjustflag=2` 的前复权日 K 做第二源校验。每只股票保存最近 180 根，只有双源日期、最新收盘价和最近复权序列通过一致性校验时，才写 `valid_for_signal_axis=true`。

观察池：600309、688012、601899、600183、603259、601138。

输出文件：
- `data/status.json`：轻量状态和异常信息；
- `data/latest.json`：六只股票完整 180 根历史数据；
- `data/<symbol>.csv`：单股历史数据。

工作流默认周一至周五 **16:20 北京时间** 执行，也可在 GitHub Actions 页面手动 `Run workflow`。

公开仓库可用固定原始地址：

```text
https://raw.githubusercontent.com/<用户名>/<仓库名>/main/data/status.json
https://raw.githubusercontent.com/<用户名>/<仓库名>/main/data/latest.json
```

建议盘前任务先读 `status.json`：`valid_for_signal_axis=true` 才用 `latest.json` 最后120根计算 S/P/T/I；否则输出数据异常提醒。

仓库只保存公开行情和代码，不需要任何 API Key，也不要放入账户、交易记录或其他私人数据。
