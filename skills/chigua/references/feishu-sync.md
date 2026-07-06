# 飞书同步模块

## 概述

飞书多维表格 (Base) 作为云端存储，实现跨设备的图谱数据同步。

同步模型：**Git 式手动 pull/push + Diff 确认 + 会话起止提醒**。

---

## 飞书 Base 表结构

JSON 的嵌套时间线结构映射为飞书的 5 张扁平表：

### 表 1: people (人物基本信息)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | 文本 | 主键，如 `p_abc123` |
| `name` | 文本 | 姓名 |
| `aliases` | 多行文本 | JSON 数组字符串 |
| `categories` | 多选 | 家人/同事/朋友/同学/网友/其他 |
| `created_at` | 日期时间 | 创建时间 |
| `updated_at` | 日期时间 | 最后更新时间 |

### 表 2: person_impressions (人物印象时间线)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | 文本 | 主键，如 `pi_abc123` |
| `person_id` | 文本 | 关联 people.id |
| `date` | 日期 | 印象日期 |
| `impression` | 多行文本 | 印象内容 |
| `source_event` | 文本 | 关联事件ID，可选 |
| `updated_at` | 日期时间 | 更新时间 |

### 表 3: events (事件)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | 文本 | 主键，如 `e_abc123` |
| `date` | 日期 | 发生日期 |
| `title` | 文本 | 事件标题 |
| `involved_people` | 多行文本 | JSON 数组字符串 |
| `summary` | 多行文本 | 摘要 |
| `my_feeling` | 文本 | 我的感受 |
| `my_state` | 文本 | 我的状态 |
| `tags` | 多选 | 标签 |
| `raw_quote` | 多行文本 | 用户原话 |
| `created_at` | 日期时间 | 创建时间 |
| `updated_at` | 日期时间 | 更新时间 |

### 表 4: relationships (关系基本信息)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | 文本 | 主键，如 `r_abc123` |
| `from` | 文本 | 固定为 "me" |
| `to` | 文本 | 关联 people.id |
| `notes` | 多行文本 | 备注 |
| `created_at` | 日期时间 | 创建时间 |
| `updated_at` | 日期时间 | 更新时间 |

### 表 5: relationship_timeline (关系演变)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | 文本 | 主键，如 `re_abc123` |
| `relationship_id` | 文本 | 关联 relationships.id |
| `date` | 日期 | 变更日期 |
| `type` | 文本 | 关系类型 |
| `intimacy` | 数字 | 亲密度 1-10 |
| `updated_at` | 日期时间 | 更新时间 |

---

## 同步流程

### Pull（云端 → 本地）

1. 用户请求或会话开始提醒："云端有比本地新的记录，是否拉取？"
2. AI 使用 `lark-base` 技能查询飞书 Base 中 `updated_at > last_pull_time` 的记录
3. 将差异以 Diff 形式展示给用户
4. 用户确认后，合并到本地 JSON。冲突策略：**云端数据覆盖本地对应记录**（因为跨设备同步场景，云端是另一个设备的最新数据）
5. 更新 `last_pull_time`

### Push（本地 → 云端）

1. 会话结束或用户主动请求时提醒："本次更新的记录尚未推送到云端，是否推送？"
2. AI 对比本地 `updated_at > last_push_time` 的记录
3. 将增量数据以 Diff 形式展示
4. 用户确认后，写入飞书 Base 各表
5. 更新 `last_push_time`

### 时间戳判定

所有同步判定基于 `updated_at` 时间戳比较。数据量小，全量比对开销可接受。

---

## 首次配置引导

技能首次运行检测到 `sync.feishu_base_id` 为 `null` 时：

1. AI 提醒："检测到尚未配置飞书云端存储。配置后可以跨设备同步图谱数据。"
2. 问用户："你有飞书账号吗？"
   - 有 → 引导在飞书中创建 Base，按本文件表结构创建 5 张表，提供 Base ID
   - 没有 → "好的，先纯本地使用，随时可以配置。"

### 引导示例

> "要配置飞书同步，请在飞书中：
> 1. 新建一个多维表格（Base）
> 2. 创建以下 5 张数据表：`people`、`person_impressions`、`events`、`relationships`、`relationship_timeline`
> 3. 每张表按我给出的字段列表添加对应列
> 4. 把 Base 的 URL 发给我（格式：https://xxx.feishu.cn/base/XXXX）"
