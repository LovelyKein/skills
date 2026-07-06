#!/usr/bin/env python3
"""
parse_ant_fund_pdf.py — 解析蚂蚁财富《基金交易明细》PDF

用法:
    python parse_ant_fund_pdf.py <pdf_path> [--out-logs <path>] [--out-holdings <path>]

输出:
    - fund-operations-log.md （操作日志，按月份/类型分类，日期倒序）
    - fund-holdings.md （持仓文件，从流水推导当前持有份额）

依赖:
    pip install pdfplumber
"""

import re
import sys
import argparse
import pathlib
from datetime import datetime
from collections import defaultdict


# ── PDF 表格提取 ──────────────────────────────────────────

# 蚂蚁财富 PDF 表头固定 12 列
_TABLE_HEADERS = [
    "order_no",      # 订单号（含日期+申请编号+交易类型+流水号，多行拼接）
    "trade_time",     # 交易时间
    "tx_type",        # 交易类型
    "fund_name",      # 基金名称
    "combo_name",     # 组合基金名称
    "fund_code",      # 基金代码
    "apply_amount",   # 申请金额
    "apply_shares",   # 申请份额
    "confirm_amount", # 确认金额
    "confirm_shares", # 确认份额
    "fee",             # 手续费
    "confirm_date",   # 确认日期
]

_BUY_TYPES = {"用户买入", "定投买入", "用户认购"}


def extract_records_from_pdf(pdf_path: str) -> list[dict]:
    """用 pdfplumber 提取表格行，返回结构化记录列表。

    pdfplumber 的 extract_tables() 能正确识别表格边框，
    避免了 pypdf 文本提取时中文字段错位/截断的问题。
    """
    try:
        import pdfplumber
    except ImportError:
        sys.exit("缺少 pdfplumber 库。请运行: pip install pdfplumber")

    records: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                for row in table:
                    if not row or not row[0]:
                        continue
                    if "订单号" in str(row[0]):
                        continue  # 跳过表头

                    # 补齐不足 12 列的行
                    padded = list(row) + [None] * (len(_TABLE_HEADERS) - len(row))
                    rec: dict = {}
                    for i, key in enumerate(_TABLE_HEADERS):
                        val = padded[i]
                        if val is None:
                            rec[key] = ""
                        else:
                            # 去掉 PDF 换行残留（如 "2026/07/0\n2" → "2026/07/02"）
                            rec[key] = str(val).replace("\n", "").strip()

                    # 基金名称：去掉断行产生的多余空格
                    if rec["fund_name"]:
                        rec["fund_name"] = re.sub(r"\s+", "", rec["fund_name"])

                    # 订单号：原始为多段拼接，取最后 8 位作为短编号
                    raw_order = rec["order_no"].replace(" ", "")
                    if len(raw_order) >= 8:
                        rec["order_no"] = raw_order[-8:]

                    records.append(rec)

    return records


# ── 数值辅助 ──────────────────────────────────────────────

def _to_float(s: str) -> float:
    """安全解析金额/份额字符串，'/' 或空返回 0。"""
    if not s or s == "/":
        return 0.0
    try:
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _parse_datetime(dt_str: str) -> datetime | None:
    """解析 '2026/07/02 11:12' 格式。"""
    if not dt_str:
        return None
    # PDF 断行可能导致 "2026/07/02" 中间多余空格
    cleaned = re.sub(r"\s+", "", dt_str)
    try:
        return datetime.strptime(cleaned, "%Y/%m/%d%H:%M")
    except ValueError:
        try:
            return datetime.strptime(cleaned, "%Y/%m/%d")
        except ValueError:
            return None


# ── 持仓推导 ──────────────────────────────────────────────

def derive_holdings(records: list[dict]) -> tuple[dict, list[dict]]:
    """从流水推导每只基金的当前持有份额。

    返回 (holdings, cleared)：
      - holdings: 仍有持仓的基金
      - cleared: 已清仓的基金（份额归零或为负）

    转换逻辑说明：
      - 跨TA转换（用户跨TA转换）：PDF 显示两行（转出+转入）
        转出行：申请金额=0.00，申请份额=数字 → 减份额
        转入行：申请金额=数字，申请份额=/ → 加份额
      - 同公司转换（用户转换）：PDF 仅显示转入行
        该行确认份额 = 转入方获得的份额 → 加份额
        ⚠ 转出方信息在 PDF 中缺失，无法自动扣除，需手动核实
    """
    funds: dict[str, dict] = defaultdict(lambda: {
        "current_shares": 0.0,
        "total_invested": 0.0,
        "total_redeemed": 0.0,
        "fund_name": "",
    })

    conversions_out_missing: list[dict] = []

    for r in records:
        code = r.get("fund_code", "")
        if not code:
            continue

        tx_type = r.get("tx_type", "")
        name = r.get("fund_name", "")
        if name:
            funds[code]["fund_name"] = name

        amt = _to_float(r.get("confirm_amount", ""))
        shares = _to_float(r.get("confirm_shares", ""))
        apply_amount = r.get("apply_amount", "")
        apply_shares = r.get("apply_shares", "")

        if tx_type in _BUY_TYPES:
            funds[code]["current_shares"] += shares
            funds[code]["total_invested"] += amt
        elif tx_type == "用户卖出":
            funds[code]["current_shares"] -= shares
            funds[code]["total_redeemed"] += amt
        elif tx_type == "用户跨TA转换":
            # 转出：申请金额为 0.00 或 /，申请份额有值
            if apply_shares and apply_shares != "/" and apply_amount in ("0.00", "0", "/", ""):
                funds[code]["current_shares"] -= shares
                funds[code]["total_redeemed"] += amt
            else:
                # 转入
                funds[code]["current_shares"] += shares
                funds[code]["total_invested"] += amt
        elif tx_type == "用户转换":
            # 同公司转换，PDF 仅显示转入行，转出方信息缺失
            funds[code]["current_shares"] += shares
            funds[code]["total_invested"] += amt
            # 记录待手动核实的转出信息
            out_shares = _to_float(apply_shares) if apply_shares and apply_shares != "/" else 0.0
            if out_shares > 0:
                conversions_out_missing.append({
                    "转入基金": code,
                    "转入基金名称": name,
                    "疑似转出份额": out_shares,
                    "交易时间": r.get("trade_time", ""),
                })

    # 四舍五入
    for code in funds:
        funds[code]["current_shares"] = round(funds[code]["current_shares"], 2)

    # 分离持仓与已清仓
    holdings = {c: d for c, d in funds.items() if d["current_shares"] > 0.01}
    cleared = [
        {**d, "fund_code": c} for c, d in funds.items() if d["current_shares"] <= 0.01
    ]

    if conversions_out_missing:
        print("\n[!] 同公司转换（用户转换）的转出方信息在 PDF 中缺失，需手动核实：")
        for item in conversions_out_missing:
            print(f"  {item['交易时间']} 转入 {item['转入基金名称']}({item['转入基金']})"
                  f"  疑似转出份额 {item['疑似转出份额']}")

    return holdings, cleared


# ── 分组 ─────────────────────────────────────────────────

def classify_records(records: list[dict]) -> dict:
    """按 '年-月' → 交易类型 分组。"""
    groups: dict = defaultdict(lambda: defaultdict(list))
    for r in records:
        dt = _parse_datetime(r.get("trade_time", ""))
        if not dt:
            continue
        month_key = dt.strftime("%Y年%m月")
        groups[month_key][r["tx_type"]].append(r)
    return groups


# ── 写入操作日志 ──────────────────────────────────────────

def write_operations_log(groups: dict, out_path: str):
    lines = [_OPERATIONS_LOG_HEADER]

    # 每个分类对应的交易类型
    type_order = [
        ("转换（含跨TA）", ["用户跨TA转换", "用户转换"]),
        ("买入", ["用户买入"]),
        ("定投", ["定投买入"]),
        ("认购", ["用户认购"]),
        ("卖出", ["用户卖出"]),
        ("分红", ["机构分红"]),
    ]

    for month in sorted(groups.keys(), reverse=True):
        lines.append(f"\n## {month}\n")

        for section_name, type_keys in type_order:
            combined = []
            for tk in type_keys:
                combined.extend(groups[month].get(tk, []))
            if not combined:
                continue
            combined.sort(key=lambda x: x.get("trade_time", ""), reverse=True)

            lines.append(f"### {section_name}\n")

            if section_name.startswith("转换"):
                lines.append(
                    "| 日期时间 | 操作 | 基金代码 | 基金名称 | 确认金额 | 确认份额 | 手续费 | 备注 |"
                )
                lines.append("| --- | --- | --- | --- | --: | --: | --: | --- |")
                for r in combined:
                    lines.append(_format_conversion_row(r))
            else:
                lines.append(
                    "| 日期时间 | 基金代码 | 基金名称 | 确认金额 | 确认份额 | 手续费 | 备注 |"
                )
                lines.append("| --- | --- | --- | --: | --: | --: | --- |")
                for r in combined:
                    lines.append(_format_simple_row(r))

    lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[OK] 操作日志已写入: {out_path}")


def _format_dt(dt_str: str) -> str:
    """'2026/07/02 11:12' → '07-02 11:12'"""
    dt = _parse_datetime(dt_str)
    if dt:
        return dt.strftime("%m-%d %H:%M")
    return dt_str


def _format_conversion_row(r: dict) -> str:
    dt = _format_dt(r.get("trade_time", ""))
    op = _map_op(r["tx_type"])
    code = r.get("fund_code", "-")
    name = r.get("fund_name", "-")
    amt = f"{_to_float(r.get('confirm_amount', '')):,.2f}"
    shares = f"{_to_float(r.get('confirm_shares', '')):,.2f}"
    fee = f"{_to_float(r.get('fee', '')):,.2f}"
    note = _map_note(r)
    return f"| {dt} | {op} | {code} | {name} | {amt} | {shares} | {fee} | {note} |"


def _format_simple_row(r: dict) -> str:
    dt = _format_dt(r.get("trade_time", ""))
    code = r.get("fund_code", "-")
    name = r.get("fund_name", "-")
    amt = f"{_to_float(r.get('confirm_amount', '')):,.2f}"
    shares = f"{_to_float(r.get('confirm_shares', '')):,.2f}"
    fee = f"{_to_float(r.get('fee', '')):,.2f}"
    return f"| {dt} | {code} | {name} | {amt} | {shares} | {fee} | |"


def _map_op(tx_type: str) -> str:
    return {
        "用户跨TA转换": "转出（跨TA）",
        "用户转换": "转换",
    }.get(tx_type, tx_type)


def _map_note(r: dict) -> str:
    tx_type = r["tx_type"]
    if tx_type == "用户跨TA转换":
        apply_amount = r.get("apply_amount", "")
        if apply_amount in ("0.00", "0", "/", ""):
            return "转出"
        return "转入"
    if tx_type == "用户转换":
        return "转入"
    return "-"


# ── 写入持仓文件 ──────────────────────────────────────────

def write_holdings(holdings: dict, cleared: list[dict], out_path: str):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        "# 基金持仓",
        "",
        "> 本文件记录基金持仓数据（持有份额/持仓金额/累计收益），不记录行情评论或当日事件。",
        "> 基金操作记录见 `fund-operations-log.md`。",
        f"> 数据基准日期：{today}（持仓金额/累计收益对应此日净值）",
        "> 数据更新规则：每个交易日后更新（查当日净值 → 计算新市值 → 覆盖旧数据）",
        "> AI口述交易或导入PDF后，同步更新持有份额及以上所有字段",
        "",
        "---",
        "",
        "## 持有基金总览",
        "",
        "| # | 基金代码 | 基金名称 | 类型 | 持有份额 | 持仓金额 | 累计收益 |",
        "|---|---|---|---|---|---|---|",
    ]

    if not holdings:
        lines.append("| （空） | — | 暂无持仓 | — | — | — | — |")
    else:
        for i, (code, data) in enumerate(sorted(holdings.items()), start=1):
            shares = data["current_shares"]
            name = data["fund_name"] or code
            lines.append(f"| {i} | {code} | {name} | — | {shares:,.2f} | — | — |")

    lines.append("| | **合计** | | | | — | — |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 各基金详细信息")
    lines.append("")
    lines.append("（基金详细档案将在信息充分后由AI补充。可通过天天基金网查询重仓股、经理、规模等。）")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 持仓结构分析")
    lines.append("")
    lines.append("（有待持仓数据后自动生成。）")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 已清仓记录")
    lines.append("")

    if cleared:
        lines.append("| 基金代码 | 基金名称 | 最终份额 | 累计投入 | 累计回收 | 备注 |")
        lines.append("|---|---|---|---|---|---|")
        for c in sorted(cleared, key=lambda x: x["fund_code"]):
            name = c.get("fund_name", c["fund_code"])
            lines.append(
                f"| {c['fund_code']} | {name} | {c['current_shares']:.2f} | "
                f"{c['total_invested']:,.2f} | {c['total_redeemed']:,.2f} | |"
            )
    else:
        lines.append("（暂无）")

    lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[OK] 持仓文件已写入: {out_path}")


# ── 操作日志头部（规则参考） ──────────────────────────────

_OPERATIONS_LOG_HEADER = """# 基金操作日志

> 由AI维护，每次交易后同步更新。
> 按日期倒序排列，交易/转换/分红等均记入。

---

## 基金操作规则参考

### 交易时间（A股基金）

| 操作时间 | 成交净值 | 举例 |
|---|---|---|
| 交易日 **15:00前** | **T日**净值 | 周一14:00买入 → 按周一净值 |
| 交易日 **15:00后** | **T+1日**净值 | 周一15:10买入 → 按周二净值 |
| 非交易日（周末/节假日） | **下一个交易日**净值 | 周六买入 → 按周一净值 |

- A股交易日：周一至周五（法定节假日休市）
- 基金每天只有一个净值，15:00收市后计算，通常18:00后公布

### 买入（申购）

- **C类份额**：免申购费
- **A类份额**：有申购费，蚂蚁上通常打1折
- 最低买入金额：蚂蚁/天天通常 **10元起**

### 卖出（赎回）

**持有时间从确认日开始算，不是从买入日算。**
确认日 = 买入日（T日）的下一个交易日（T+1）

| 持有时间 | C类赎回费 | 说明 |
|---|---|---|
| < 7天 | **1.50%**（惩罚性） | 持仓中任何基金7天内卖出都会扣 |
| 7-30天 | 通常 0.50%~0.75% | 具体看各基金合同 |
| ≥ 30天 | C类通常 **免赎回费** | |

赎回到账：T+2 ~ T+4 个交易日。

### 转换

- 转换 = 赎回A + 申购B，按同一确认日净值计算
- **同公司转换**：持有时间延续计算
- **跨TA转换**：转出基金计算其赎回费，转入基金持有期从确认日重新开始

### 净值说明

- 基金净值盘中没有实时值（ETF除外），蚂蚁上的"估算净值"仅供参考
- 指数型基金估算误差通常较小；主动管理型可能偏差

---

## 自检机制

每次输出简报/更新数据前执行：

1. **日志完整性**：持仓清单是否全部在日志中有首次买入记录
2. **数据一致性**：日志推导份额 vs 文件记录份额
3. **手动修改检测**：检测到用户手动改过数据 → 以用户值为准
4. **净值缺失处理**：某基金净值查不到 → 告知用户
5. **日期断层**：日志中有连续空白期 → 提醒用户

---

## 数据更新流程

### A. PDF导入更新
运行 `python parse_ant_fund_pdf.py <pdf路径>` 自动生成操作日志和持仓文件。

### B. 每日持仓数据更新（简报中自动执行）
读取基准日期 → 判断间隔 → 逐日累积净值 → 更新持仓金额和累计收益。

### C. 口述操作更新
用户按格式告知交易后，AI 写入对应分类表。

---"""


# ── 主入口 ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="解析蚂蚁财富基金交易明细 PDF")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--out-logs", default="fund-operations-log.md",
                        help="操作日志输出路径 (默认: fund-operations-log.md)")
    parser.add_argument("--out-holdings", default="fund-holdings.md",
                        help="持仓文件输出路径 (默认: fund-holdings.md)")
    args = parser.parse_args()

    if not pathlib.Path(args.pdf_path).exists():
        sys.exit(f"文件不存在: {args.pdf_path}")

    print(f"[1/3] 读取 PDF: {args.pdf_path}")
    records = extract_records_from_pdf(args.pdf_path)
    if not records:
        sys.exit("未提取到任何交易记录，请确认 PDF 格式是否为蚂蚁财富交易明细。")
    print(f"       共解析到 {len(records)} 条交易记录")

    print(f"[2/3] 推导持仓...")
    holdings, cleared = derive_holdings(records)
    print(f"       持仓 {len(holdings)} 只，已清仓 {len(cleared)} 只")

    print(f"[3/3] 写入文件...")
    groups = classify_records(records)
    write_operations_log(groups, args.out_logs)
    write_holdings(holdings, cleared, args.out_holdings)

    print("\n[OK] 完成。后续可由 AI 通过天天基金网查询净值，更新持仓金额和累计收益。")


if __name__ == "__main__":
    main()
